# Miro Community - Easiest way to make a video website
#
# Copyright (C) 2010, 2011, 2012 Participatory Culture Foundation
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

from django import forms
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from tagging.forms import TagField

from localtv.models import Video, Category
from localtv.playlists.models import Playlist


class VideoForm(forms.ModelForm):
    thumbnail = forms.ImageField(label="Upload a thumbnail", required=False)
    tags = TagField(required=False, widget=forms.Textarea)
    categories = Video._meta.get_field('categories').formfield(help_text=None,
                                            widget=forms.CheckboxSelectMultiple)
    authors = Video._meta.get_field('authors').formfield(help_text=None,
                                            widget=forms.CheckboxSelectMultiple)
    video_url = forms.URLField(verify_exists=False)

    class Meta:
        model = Video
        fields = ('name', 'description', 'thumbnail_url', 'categories',
                  'authors', 'when_published', 'file_url', 'embed_code',)

    def save(self, *args, **kwargs):
        thumbnail = self.cleaned_data.pop('thumbnail', None)
        thumbnail_url = self.cleaned_data.pop('thumbnail_url', None)

        if thumbnail:
            self.instance.thumbnail_url = ''
            # since we're no longer using
            # that URL for a thumbnail
            self.instance.save_thumbnail_from_file(thumbnail)
        elif thumbnail_url and 'thumbnail_url' in self.changed_data:
            try:
                self.instance.save_thumbnail()
            except models.CannotOpenImageUrl:
                pass # we'll get it in a later update
        return forms.ModelForm.save(self, *args, **kwargs)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        exclude = ['site', 'contest_mode']

    def __init__(self, *args, **kwargs):
        super(CategoryForm, self).__init__(*args, **kwargs)
        self.fields['parent'].queryset = Category.objects.filter(
            site=Site.objects.get_current())

    def clean_parent(self):
        parent = self.cleaned_data['parent']
        while parent is not None:
            if parent == self.instance:
                raise ValidationError("A category cannot be its own parent.")
            parent = parent.parent
        return self.cleaned_data['parent']

    def _post_clean(self):
        self._validate_unique = False
        if self.instance.pk is None:
            self.instance.site = Site.objects.get_current()
        super(CategoryForm, self)._post_clean()
        try:
            self.instance.validate_unique()
        except ValidationError, e:
            self._update_errors(e.message_dict)


class PlaylistForm(forms.ModelForm):
    class Meta:
        model = Playlist
        fields = ['name', 'slug', 'description']

    def _post_clean(self):
        self._validate_unique = False
        super(PlaylistForm, self)._post_clean()
        try:
            self.instance.validate_unique()
        except ValidationError, e:
            self._update_errors(e.message_dict)


class PlaylistCreateForm(PlaylistForm):
    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(PlaylistCreateForm, self).__init__(*args, **kwargs)

    def _post_clean(self):
        self.instance.user = self.user
        super(PlaylistCreateForm, self)._post_clean()