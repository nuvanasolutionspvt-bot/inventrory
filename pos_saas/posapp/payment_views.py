from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from .forms import RazorpaySettingsForm
from .models import Tenant, TenantFeature, TenantPaymentGateway, Sale, SalePaymentOrder
from .tenancy import require_active_tenant
from .payments import (PaymentError, gateway_request, decrypt_secret, encrypt_secret,
    payment_access, get_or_create_order, order_gateway, verify_signature, settle_payment)


@sensitive_post_parameters('key_secret')
@sensitive_variables('secret')
@login_required
@transaction.atomic
def payment_module(request):
    tenant = require_active_tenant(request)
    if tenant.business_type != 'restaurant' or not TenantFeature.objects.filter(tenant=tenant, online_payment=True).exists():
        raise PermissionDenied('Online Payment is not enabled for this restaurant.')
    payment_access(request, tenant)
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    gateway = TenantPaymentGateway.objects.filter(tenant=tenant).first()
    can_configure = request.user.has_perm('posapp.can_manage_settings')
    form = None
    if request.method == 'POST' and not can_configure:
        raise PermissionDenied('Settings permission is required to change merchant credentials.')
    if can_configure:
        form = RazorpaySettingsForm(request.POST or None, gateway=gateway, initial={
            'key_id': gateway.key_id if gateway else '', 'enabled': gateway.enabled if gateway else False,
        })
        if request.method == 'POST' and form.is_valid():
            changed = not gateway or form.cleaned_data['key_id'] != gateway.key_id or bool(form.cleaned_data['key_secret'])
            if changed and SalePaymentOrder.objects.filter(tenant=tenant, status='created').exists():
                form.add_error(None, 'Resolve pending payments before changing merchant keys. Use Check payment status for pending bills.')
            else:
                try:
                    secret = form.cleaned_data['key_secret'] or decrypt_secret(gateway)
                    result = gateway_request(form.cleaned_data['key_id'], secret, 'GET', 'orders?count=1')
                    if not isinstance(result.get('items'), list):
                        raise PaymentError('Razorpay returned an invalid credential-validation response.')
                    encrypted = encrypt_secret(secret)
                except PaymentError as exc:
                    form.add_error(None, str(exc))
                else:
                    TenantPaymentGateway.objects.update_or_create(tenant=tenant, defaults={
                        'key_id': form.cleaned_data['key_id'], 'encrypted_secret': encrypted,
                        'enabled': form.cleaned_data['enabled'], 'verified_at': timezone.now(),
                    })
                    messages.success(request, 'Razorpay credentials verified and saved.')
                    return redirect('restaurant_module_page', module='online-payment')
    return render(request, 'restaurant/payments.html', {
        'gateway': gateway, 'form': form,
        'open_bills': Sale.objects.filter(tenant=tenant, order_status='open', is_return=False, paid_amount=0, total__gte=1).order_by('-pk')[:50],
        'payment_orders': SalePaymentOrder.objects.filter(tenant=tenant).select_related('sale')[:50],
    })


@login_required
def payment_checkout(request, sale_id):
    tenant = require_active_tenant(request)
    payment_access(request, tenant)
    sale = get_object_or_404(Sale, tenant=tenant, pk=sale_id)
    if sale.order_status == Sale.ORDER_STATUS_PAID:
        return redirect('invoice_view', sale_id=sale.pk)
    gateway = TenantPaymentGateway.objects.filter(tenant=tenant).first()
    return render(request, 'sales/razorpay_checkout.html', {
        'sale': sale, 'gateway': gateway,
        'payment_urls': {'create': reverse('razorpay_order', args=[sale.pk]),
                         'verify': reverse('razorpay_verify', args=[sale.pk]),
                         'check': reverse('razorpay_check', args=[sale.pk])},
    })


def locked_sale(request, sale_id):
    tenant = require_active_tenant(request)
    payment_access(request, tenant)
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    return get_object_or_404(Sale.objects.select_for_update(), tenant=tenant, pk=sale_id)


@login_required
@require_POST
@transaction.atomic
def razorpay_order(request, sale_id):
    sale = locked_sale(request, sale_id)
    try:
        order = get_or_create_order(sale, request.user)
        if order.status == 'paid':
            return JsonResponse({'redirect': reverse('invoice_view', args=[sale.pk]) + '?print=1'})
        return JsonResponse({'key': order.key_id, 'order_id': order.gateway_order_id,
            'amount': int(order.amount * 100), 'currency': order.currency, 'name': sale.tenant.name,
            'description': f'Bill INV-{sale.pk}'})
    except PaymentError as exc:
        return JsonResponse({'error': str(exc)}, status=400)


def verification_response(request, sale_id, *, callback):
    # All lookups and settlement are within the enclosing view's atomic transaction.
    sale = locked_sale(request, sale_id)
    order = get_object_or_404(SalePaymentOrder.objects.select_for_update(), tenant=sale.tenant, sale=sale)
    try:
        if callback:
            if request.POST.get('razorpay_order_id') != order.gateway_order_id:
                raise PaymentError('Payment order does not match this bill.')
            payment_id = request.POST.get('razorpay_payment_id', '')
            verify_signature(order, payment_id, request.POST.get('razorpay_signature', ''))
            if order.status == 'paid' and order.payment_id != payment_id:
                raise PaymentError('The bill was paid with a different payment.')
        if order.status != 'paid':
            gateway = order_gateway(order)
            if callback:
                payment = gateway_request(gateway.key_id, decrypt_secret(gateway), 'GET', f'payments/{payment_id}')
                if payment.get('id') != payment_id:
                    raise PaymentError('Payment identity mismatch.')
            else:
                result = gateway_request(gateway.key_id, decrypt_secret(gateway), 'GET', f'orders/{order.gateway_order_id}/payments')
                items = result.get('items', [])
                if not isinstance(items, list):
                    raise PaymentError('Razorpay returned an invalid payment status response.')
                payment = next((p for status in ('captured', 'authorized') for p in items
                                if isinstance(p, dict) and p.get('status') == status), None)
                if payment is None:
                    failed = next((p for p in items if isinstance(p, dict) and p.get('status') == 'failed'), None)
                    if failed:
                        description = str(failed.get('error_description') or '').lower()
                        if 'website' in description and ('registered' in description or 'match' in description):
                            raise PaymentError('Razorpay blocked this payment because this website does not match the merchant account registered website. Update the website/domain in Razorpay or use the registered website. The bill is still unpaid.')
                        raise PaymentError('Razorpay reports this payment attempt as failed. No successful payment was found; the bill is still unpaid. Check the failure details in your Razorpay dashboard before retrying.')
                    raise PaymentError('No captured or authorized payment yet. The bill remains open.')
            settle_payment(order, sale, payment)
        return JsonResponse({'redirect': reverse('invoice_view', args=[sale.pk]) + '?print=1'})
    except PaymentError as exc:
        # Capture may have succeeded remotely. Keep the pending record for safe reconciliation.
        transaction.set_rollback(True)
        return JsonResponse({'error': str(exc)}, status=400)


@login_required
@require_POST
@transaction.atomic
def razorpay_verify(request, sale_id):
    return verification_response(request, sale_id, callback=True)


@login_required
@require_POST
@transaction.atomic
def razorpay_check(request, sale_id):
    return verification_response(request, sale_id, callback=False)