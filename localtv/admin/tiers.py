# Copyright 2010 - Participatory Culture Foundation
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

import re
import datetime
import urllib

from django.contrib.auth.models import User
from django.core.paginator import Paginator, InvalidPage
from django.db.models import Q
from django.db import transaction
from django.http import Http404, HttpResponseRedirect, HttpResponse, HttpResponseForbidden
from django.shortcuts import render_to_response, get_object_or_404
from django.template.context import RequestContext
from django.utils.encoding import force_unicode
from django.views.decorators.csrf import csrf_protect, csrf_exempt
from django.core.urlresolvers import reverse
from django.conf import settings

import paypal.standard.ipn.views

from localtv.decorators import require_site_admin
from localtv import models
from localtv.util import SortHeaders, MockQueryset
from localtv.admin import forms

import localtv.tiers
import localtv.paypal_snippet

### Below this line
### ----------------------------------------------------------------------
### These are admin views that the user will see at /admin/*

@require_site_admin
@csrf_protect
def confirmed_change_tier(request):
    '''The point of this function is to provide somewhere for the PayPal form to POST
    to -- instead of PayPal. So we start by asserting that PayPal is skipped, and then
    we simply change the tier, and redirect back to the site level admin page.'''
    skip_paypal = getattr(settings, "LOCALTV_SKIP_PAYPAL", False)
    assert skip_paypal

    target_tier_name = request.POST.get('target_tier_name', '')

    # validate
    if target_tier_name not in dict(localtv.tiers.CHOICES):
        # Always redirect back to tiers page
        return HttpResponseRedirect(reverse('localtv_admin_tier'))

    return _actually_switch_tier(target_tier_name)

@require_site_admin
def downgrade_confirm(request):
    target_tier_name = request.POST.get('target_tier_name', None)
    # validate
    if target_tier_name in dict(localtv.tiers.CHOICES):
        target_tier_obj = localtv.tiers.Tier(target_tier_name)

        would_lose = localtv.tiers.user_warnings_for_downgrade(target_tier_name)
        data = {}
        data['tier_name'] = target_tier_name
        data['paypal_sandbox'] = getattr(settings, 'PAYPAL_TEST', False)
        p = localtv.paypal_snippet.PayPal.get_with_django_settings()
        data['paypal_url'] = p.PAYPAL_URL
        data['paypal_email'] = getattr(settings, 'PAYPAL_RECEIVER_EMAIL', '')
        data['target_tier_obj'] = target_tier_obj
        data['would_lose_admin_usernames'] = localtv.tiers.push_number_of_admins_down(target_tier_obj.admins_limit())
        data['customtheme_nag'] = ('customtheme' in would_lose)
        data['advertising_nag'] = ('advertising' in would_lose)
        data['customdomain_nag'] = ('customdomain' in would_lose)
        data['css_nag'] = ('css' in would_lose)
        data['videos_nag'] = ('videos' in would_lose)
        data['videos_over_limit'] = localtv.tiers.hide_videos_above_limit(target_tier_obj)
        data['new_theme_name'] = localtv.tiers.switch_to_a_bundled_theme_if_necessary(target_tier_obj)
        data['payment_secret'] = request.tier_info.get_payment_secret()
        return render_to_response('localtv/admin/downgrade_confirm.html', data,
                                  context_instance=RequestContext(request))

    # In some weird error case, redirect back to tiers page
    return HttpResponseRedirect(reverse('localtv_admin_tier'))

@require_site_admin
@csrf_protect
def upgrade(request):
    SWITCH_TO = 'Switch to this'
    UPGRADE = 'Upgrade Your Account'

    switch_messages = {}
    if request.sitelocation.tier_name in ('premium', 'max'):
        switch_messages['plus'] = SWITCH_TO
    else:
        switch_messages['plus'] = UPGRADE

    if request.sitelocation.tier_name == 'max':
        switch_messages['premium'] = SWITCH_TO
    else:
        switch_messages['premium'] = UPGRADE

    # Would you lose anything?
    would_lose = {}
    for tier_name in ['basic', 'plus', 'premium', 'max']:
        if tier_name == request.sitelocation.tier_name:
            would_lose[tier_name] = False
        else:
            would_lose[tier_name] = localtv.tiers.user_warnings_for_downgrade(tier_name)

    data = {}
    data['site_location'] = request.sitelocation
    data['would_lose_for_tier'] = would_lose
    data['switch_messages'] = switch_messages
    data['payment_secret'] = request.tier_info.get_payment_secret()
    data['offer_free_trial'] = request.tier_info.free_trial_available
    data['skip_paypal'] = getattr(settings, 'LOCALTV_SKIP_PAYPAL', False)
    if not data['skip_paypal']:
        p = localtv.paypal_snippet.PayPal.get_with_django_settings()
        data['paypal_url'] = p.PAYPAL_URL

    return render_to_response('localtv/admin/upgrade.html', data,
                              context_instance=RequestContext(request))

### Below this line:
### -------------------------------------------------------------------------
### These functions are resquest handlers that actually switch tier.

@csrf_exempt
def paypal_return(request):
    '''This view is where PayPal sends users to upon success. Some things to note:

    * PayPal sends us an "auth" parameter that we cannot validate.
    * This should be a POST so that cross-site scripting can't just upgrade people's sites.
    * This is not as secure as I would like.

    Suggested improvements:
    * The view that sends people to PayPal should store some state in the database
      that this view checks. It only permits an upgrade in that situation.
    * That could be the internal "payment_secret" to prevent CSRF.
    * A tricky site admin could still try POST the right data to this view, which would
      trigger the tier change.

    If you want to exploit a MC site and change its tier, and you can cause an admin
    with a cookie that's logged-in to visit pages you want, and you can get that admin
    to do a POST, you still have to POST a value for the "auth" key. Note that this is
    why we do a sanity-check of tier+payment status every night; we will catch funny
    business within a day or so.'''
    auth = request.POST.get('auth', None) or request.GET.get('auth', None)
    if not auth:
        return HttpResponseForbidden("You failed to submit an 'auth' token.")
    return HttpResponseRedirect(reverse('localtv_admin_tier'))

@csrf_exempt
@require_site_admin
def begin_free_trial(request, payment_secret):
    '''This is where PayPal sends the user, if they are going to begin a free trial.

    At this stage, we do not know what tier the user wanted to opt into. That should be stored
    in the ?target_tier_name=... GET parameter.

    If it is some nonsense, we should show an obscure error message and tell them to email
    questions@MC if they got it.

    If it what we expect, then:

    * For now, trust that the IPN process will happen in the background,

    * Declare the free trial in-use, and

    * Switch the tier.'''
    if payment_secret != request.tier_info.payment_secret:
        raise HttpResponseForbidden("You are accessing this URL with invalid parameters. If you think you are seeing this message in error, email questions@mirocommunity.org")
    target_tier_name = request.GET.get('target_tier_name', '')
    if target_tier_name not in dict(localtv.tiers.CHOICES):
        return HttpResponse("Something went wrong switching your site level. Please send an email to questions@mirocommunity.org immediately.")

    # This is so that we can detect sites that start a free trial, but never generate
    # the IPN event.
    if request.tier_info.free_trial_started_on is None:
        request.tier_info.free_trial_started_on = datetime.datetime.utcnow()

    # Set the free trial to be in-use.
    if request.tier_info.free_trial_available:
        request.tier_info.free_trial_available = False
        request.tier_info.save()

    # Switch the tier!
    return _actually_switch_tier(request, target_tier_name)

### Below this line
### --------------------------------------------------------------------------------------------
### This function is something PayPal POSTs updates to.

@csrf_exempt
def ipn_endpoint(request, payment_secret):
    # PayPal sends data to this function via POST.
    #
    # At this point in processing, the data might be fake. Let's pass it to
    # the django-paypal code and ask it to verify it for us.
    if payment_secret == request.tier_info.payment_secret:
        response = paypal.standard.ipn.views.ipn(request)
        return response
    return HttpResponseForbidden("You submitted something invalid to this IPN handler.")

### Below this line
### ----------------------------------------------------------------------
### These are helper functions.

def downgrade_paypal_monthly_subscription(tier_info, target_amount):
    # FIXME: If the target amount is zero, cancel it
    return True # FIXME: Implement with PayPal NVP API

def _actually_switch_tier(request, target_tier_name):
    # Is there a monthly payment going on? If so, we should make sure its amount
    # is appropriate.
    target_tier_obj = localtv.tiers.Tier(target_tier_name)

    if getattr(settings, "LOCALTV_SKIP_PAYPAL", False):
        pass
    else:
        if False:
            target_amount = target_tier_obj.dollar_cost()
            
            current_amount = get_monthly_amount_of_paypal_subscription(request.tier_info.current_paypal_profile_id)

            if target_amount > current_amount:
                # Eek -- in this case, we cannot proceed.
                raise ValueError, "The existing PayPal ID needs to be upgraded."

            if target_amount < current_amount:
                downgrade_paypal_monthly_subscription(request.tier_info, target_amount)

    # Okay, the money downgrade worked. Thank heavens.
    #
    # Now it's safe to proceed with the internal tier switch.
    sl = request.sitelocation
    sl.tier_name = target_tier_name
    sl.save()

    # Always redirect back to tiers page
    return HttpResponseRedirect(reverse('localtv_admin_tier'))

from paypal.standard.ipn.signals import subscription_signup, subscription_cancel, subscription_eot, subscription_modify
def handle_recurring_profile_start(sender, **kwargs):
    ipn_obj = sender

    # If the thing is invalid, do not process any further.
    if ipn_obj.flag:
        return

    tier_info = localtv.models.TierInfo.objects.get_current()
    tier_info.current_paypal_profile_id = ipn_obj.subscr_id
    tier_info.save()

    # If we get the IPN, and we have not yet adjusted the tier name
    # to be at that level, now is a *good* time to do so.
    amount = float(ipn_obj.amount3)
    sitelocation = localtv.models.SiteLocation.objects.get_current()
    if sitelocation.get_tier().dollar_cost() == amount:
        pass
    else:
        # Find the right tier to move to
        tier_name = localtv.tiers.Tier.get_by_cost(amount)
        sitelocation.tier_name = tier_name
        sitelocation.save()

subscription_signup.connect(handle_recurring_profile_start)

def on_subscription_cancel_switch_to_basic(sender, **kwargs):
    ipn_obj = sender

    # If the thing is invalid, do not process any further.
    if ipn_obj.flag:
        return

    sitelocation = localtv.models.SiteLocation.objects.get_current()
    sitelocation.tier_name = 'basic'
    sitelocation.save()

    # Delete the current paypal subscription ID
    tier_info = localtv.models.TierInfo.objects.get_current()
    tier_info.current_paypal_profile_id = ''
    tier_info.payment_due_date = None
    tier_info.save()
subscription_cancel.connect(on_subscription_cancel_switch_to_basic)
subscription_eot.connect(on_subscription_cancel_switch_to_basic)
subscription_modify.connect(handle_recurring_profile_start)
