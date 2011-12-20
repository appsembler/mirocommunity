# Copyright 2009 - Participatory Culture Foundation
#
# This file is part of Miro Community.
#
# Miro Community is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# Miro Community is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Miro Community.  If not, see <http://www.gnu.org/licenses/>.

from django.conf import settings
from django.core.urlresolvers import reverse
from django.forms.models import modelform_factory
from django.http import HttpResponseRedirect, Http404
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.views.decorators.csrf import csrf_protect
from django.views.generic import FormView, CreateView
from django.utils.decorators import method_decorator
from tagging.utils import parse_tag_input
import vidscraper

from localtv.decorators import request_passes_test
from localtv.models import SiteLocation, Video
from localtv.signals import submit_finished
from localtv.submit_video.forms import SubmitURLForm, SubmitVideoForm
from localtv.submit_video.utils import is_video_url
from localtv.utils import get_or_create_tags


def _has_submit_permissions(request):
    sitelocation = SiteLocation.objects.get_current()
    if not sitelocation.submission_requires_login:
        return True
    else:
        if sitelocation.display_submit_button:
            return request.user.is_authenticated() and request.user.is_active
        else:
            return request.user_is_admin()


class SubmitURLView(FormView):
    form_class = SubmitURLForm
    session_key = "localtv_submit_video_info"
    template_name = "localtv/submit_video/submit.html"

    @method_decorator(request_passes_test(_has_submit_permissions))
    def get(self, request, *args, **kwargs):
        return FormView.post(self, request, *args, **kwargs)

    @method_decorator(csrf_protect)
    def post(self, request, *args, **kwargs):
        # This method should be disallowed. Some forms may still use it in old
        # templates, so we handle it for backwards-compatibility.
        return self.get(request, *args, **kwargs)

    def get_session_key(self):
        return self.session_key

    def get_form(self, form_class):
        kwargs = self.get_form_kwargs()
        # If it looks like the form has been submitted via GET, set the data
        # kwarg to the GET data.
        if set(self.request.GET) & set(form_class.base_fields):
            kwargs['data'] = self.request.GET
        return form_class(**kwargs)

    def form_valid(self, form):
        video = form.video_cache
        url = form.cleaned_data['url']
        # This bit essentially just preserves the old behavior; really, the
        # views that are redirected to are all instances of SubmitVideoView.

        if video is not None and (video.embed_code or
                (video.file_url and not video.file_url_expires)):
            success_url = reverse('localtv_submit_scraped_video')
        elif is_video_url(url):
            success_url = reverse('localtv_submit_directlink_video')
        else:
            success_url = reverse('localtv_submit_embedrequest_video')

        self.success_url = "%s?%s" % (success_url, self.request.GET.urlencode())

        key = self.get_session_key()
        self.request.session[key] = {
            'video': video,
            'url': url
        }
        return super(SubmitURLView, self).form_valid(form)

    def get_context_data(self, **kwargs):
        context = super(SubmitURLView, self).get_context_data(**kwargs)
        # HACK to provide backwards-compatible context.
        context['was_duplicate'] = getattr(context['form'], 'was_duplicate',
                                           False)
        context['video'] = getattr(context['form'], 'duplicate_video', None)
        return context


class SubmitVideoView(CreateView):
    form_class = SubmitVideoForm
    session_key = "localtv_submit_video_info"

    @method_decorator(request_passes_test(_has_submit_permissions))
    @method_decorator(csrf_protect)
    def dispatch(self, request, *args, **kwargs):
        session_key = self.get_session_key()
        if (session_key not in request.session or
            not request.session[session_key].get('url', None)):
            return HttpResponseRedirect(reverse('localtv_submit_video'))
        return super(SubmitVideoView, self).dispatch(request, *args, **kwargs)

    def get_session_key(self):
        return self.session_key

    def get_success_url(self):
        return reverse('localtv_submit_thanks', args=[self.object.pk])

    def get_form_class(self):
        fields = ['tags', 'contact', 'notes']
        session_key = self.get_session_key()
        try:
            session_dict = self.request.session[session_key]
            self.video = session_dict['video']
            self.url = session_dict['url']
        except KeyError:
            raise Http404

        if self.video is not None and (self.video.embed_code or
                (self.video.file_url and not self.video.file_url_expires)):
            pass
        elif is_video_url(self.url):
            fields += ['name', 'description', 'website_url']
        else:
            fields += ['name', 'description', 'embed_code']

        if self.video is not None:
            self.object = Video.from_vidscraper_video(self.video, commit=False)

        return modelform_factory(Video, form=self.form_class, fields=fields)

    def get_initial(self):
        initial = super(SubmitVideoView, self).get_initial()
        if self.video.tags:
            initial.update({
                'tags': get_or_create_tags(self.video.tags),
            })
        return initial

    def get_form_kwargs(self):
        kwargs = super(SubmitVideoView, self).get_form_kwargs()
        kwargs.update({
            'request': self.request,
            'url': self.url
        })
        return kwargs

    def get_template_names(self):
        if self.video is not None and (self.video.embed_code or
                (self.video.file_url and not self.video.file_url_expires)):
            template_names = ['localtv/submit_video/scraped.html']
        elif is_video_url(self.url):
            template_names = ['localtv/submit_video/direct.html']
        else:
            template_names = ['localtv/submit_video/embed.html']

        return template_names

    def form_valid(self, form):
        response = super(SubmitVideoView, self).form_valid(form)
        del request.session[self.get_session_key()]
        submit_finished.send(sender=self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super(SubmitVideoView, self).get_context_data(**kwargs)
        # Provided for backwards-compatibility.
        context['data'] = {
            'link': self.object.website_url,
            'publish_date': self.object.when_published,
            'tags': parse_tag_input(context['form'].initial.get('tags', '')),
            'title': self.object.name,
            'description': self.object.description,
            'thumbnail_url': self.object.thumbnail_url,
            'user': self.object.video_service_user,
            'user_url': self.object.video_service_url,
        }
        return context


def submit_thanks(request, video_id=None):
    if request.user_is_admin() and video_id:
        context = {
            'video': Video.objects.get(pk=video_id)
            }
    else:
        context = {}
    return render_to_response(
        'localtv/submit_video/thanks.html', context,
        context_instance=RequestContext(request))
