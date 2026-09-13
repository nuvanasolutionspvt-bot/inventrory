import json
import urllib.request
import urllib.error
from django import forms
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.decorators.http import require_http_methods


class ContactForm(forms.Form):
    name = forms.CharField(max_length=150)
    phone = forms.RegexField(regex=r"^[+0-9(). -]{7,32}$", max_length=32)
    email = forms.EmailField(max_length=254)
    business_type = forms.ChoiceField(choices=[(v, v) for v in (
        'restaurant', 'cafe', 'food_truck', 'cloud_kitchen', 'bakery', 'catering', 'other')])
    message = forms.CharField(max_length=5000)
    whatsapp_opt_in = forms.BooleanField(required=False)


@require_http_methods(['GET', 'POST'])
def contact_us(request):
    form = ContactForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        url = settings.CONTACT_APPS_SCRIPT_URL
        token = settings.CONTACT_APPS_SCRIPT_TOKEN
        if not url or not token:
            form.add_error(None, 'Contact submissions are temporarily unavailable. Please try again later.')
        else:
            payload = dict(form.cleaned_data, token=token)
            try:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(req, timeout=20) as response:
                    result = json.loads(response.read(65536))
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise ValueError('Submission not acknowledged')
            except (urllib.error.URLError, TimeoutError, ValueError, OSError):
                form.add_error(None, 'We could not confirm your submission. Please try again shortly.')
            else:
                messages.success(request, 'Thank you! Your enquiry has been received.')
                return redirect('contact_us')
    return render(request, 'contact_us.html', {'form': form})
