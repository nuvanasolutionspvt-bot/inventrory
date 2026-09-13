import json
import logging
import urllib.request
import urllib.error
from django.http import JsonResponse
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


logger = logging.getLogger(__name__)


@require_http_methods(['GET', 'POST'])
def contact_us(request):
    form = ContactForm(request.POST if request.method == 'POST' else None)
    wants_json = 'application/json' in request.headers.get('Accept', '')

    def failure(code, message, status, **details):
        logger.warning('Contact submission failed: %s', code)
        if wants_json:
            return JsonResponse({'ok': False, 'code': code, 'message': message, **details}, status=status)
        form.add_error(None, f'{message} ({code})')
        return render(request, 'contact_us.html', {'form': form}, status=status)

    if request.method == 'POST':
        if not form.is_valid():
            return failure('VALIDATION_ERROR', 'Please check the form fields.', 400,
                           errors=form.errors.get_json_data())
        url = settings.CONTACT_APPS_SCRIPT_URL.strip()
        token = settings.CONTACT_APPS_SCRIPT_TOKEN.strip()
        missing = [name for name, value in [('CONTACT_APPS_SCRIPT_URL', url),
                   ('CONTACT_APPS_SCRIPT_TOKEN', token)] if not value]
        if missing:
            return failure('CONTACT_CONFIG_MISSING',
                'Contact service configuration is incomplete.', 503, missing_settings=missing)
        try:
            req = urllib.request.Request(url, data=json.dumps(dict(form.cleaned_data, token=token)).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=20) as response:
                if urllib.parse.urlparse(response.geturl()).hostname == 'accounts.google.com':
                    return failure('GOOGLE_SIGN_IN_REQUIRED', 'Google requires sign-in. Check web app access is Anyone.', 502)
                result = json.loads(response.read(65536))
        except urllib.error.HTTPError as exc:
            return failure('GOOGLE_HTTP_ERROR', 'Google rejected the request. Check deployment access.', 502,
                           upstream_status=exc.code)
        except TimeoutError:
            return failure('GOOGLE_TIMEOUT', 'Google did not respond in time. Submission could not be confirmed.', 504)
        except urllib.error.URLError:
            return failure('GOOGLE_CONNECTION_ERROR', 'The server could not connect to Google.', 502)
        except (ValueError, UnicodeError):
            return failure('GOOGLE_INVALID_RESPONSE', 'Google did not return valid JSON. Check the deployed script and access settings.', 502)
        except OSError:
            return failure('GOOGLE_CONNECTION_ERROR', 'The server could not connect to Google.', 502)
        if not isinstance(result, dict) or result.get('ok') is not True:
            error_messages = {
                'SCRIPT_TOKEN_MISSING': 'Apps Script has no CONTACT_TOKEN script property. Add it in Project Settings.',
                'TOKEN_MISMATCH': 'Apps Script CONTACT_TOKEN does not match the token loaded by Django.',
                'INVALID_FIELD': 'Apps Script rejected a required form field.',
                'INVALID_JSON': 'Apps Script could not parse the submitted JSON.',
                'SHEET_WRITE_FAILED': 'Apps Script could not save the enquiry. Open Apps Script Executions to see the sheet error.',
            }
            upstream_code = result.get('code') if isinstance(result, dict) else None
            if isinstance(upstream_code, str) and upstream_code in error_messages:
                details = {'upstream_code': upstream_code}
                field = result.get('field')
                if isinstance(field, str) and field in ContactForm.base_fields:
                    details['field'] = field
                return failure(upstream_code, error_messages[upstream_code], 502, **details)
            return failure('GOOGLE_ERROR_DETAILS_MISSING',
                'The deployed Apps Script returned no recognized error code. Deploy the updated Code.gs as a new version.', 502,
                upstream_code='UNKNOWN')
        if wants_json:
            return JsonResponse({'ok': True, 'message': 'Thank you! Your enquiry has been received.'})
        messages.success(request, 'Thank you! Your enquiry has been received.')
        return redirect('contact_us')
    return render(request, 'contact_us.html', {'form': form})
