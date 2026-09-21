"""Tenant merchant payments, separate from platform subscription credentials."""
import hashlib
import hmac
import re
from decimal import Decimal

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .models import (TenantPaymentGateway, TenantFeature, SalePaymentOrder, Sale,
                     CustomerLedger, RestaurantTable)


class PaymentError(Exception):
    """Safe, user-facing gateway error without credentials or raw provider responses."""


def cipher():
    try:
        return Fernet(settings.PAYMENT_CREDENTIAL_KEY.encode('ascii'))
    except (ValueError, TypeError, UnicodeError):
        raise PaymentError('Payment credential encryption is not configured. Contact the system administrator.') from None


@sensitive_variables('secret')
def encrypt_secret(secret):
    return cipher().encrypt(secret.encode('utf-8')).decode('ascii')


@sensitive_variables()
def decrypt_secret(gateway):
    try:
        return cipher().decrypt(gateway.encrypted_secret.encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeError):
        raise PaymentError('Saved payment credentials could not be decrypted. Contact the system administrator.') from None


@sensitive_variables()
def gateway_request(key_id, secret, method, path, payload=None):
    try:
        response = requests.request(method, 'https://api.razorpay.com/v1/' + path,
            auth=(key_id, secret), json=payload, timeout=(5, 20), allow_redirects=False)
        if response.status_code in (401, 403):
            raise PaymentError('Razorpay rejected the Key ID and Key Secret. Check the account and test/live key pair.')
        if not 200 <= response.status_code < 300:
            raise PaymentError('Razorpay could not complete this request. Check payment status before trying again.')
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (requests.RequestException, ValueError):
        raise PaymentError('Razorpay is unavailable or returned an invalid response. Check payment status before retrying.') from None


def checkout_available(tenant):
    return (tenant.business_type == 'restaurant'
        and TenantFeature.objects.filter(tenant=tenant, online_payment=True).exists()
        and TenantPaymentGateway.objects.filter(tenant=tenant, enabled=True, verified_at__isnull=False).exists())


def payment_access(request, tenant):
    if (tenant.business_type != 'restaurant' or not request.user.has_perm('posapp.can_pos')
            or request.user.groups.filter(name='Waiter').exists()):
        raise PermissionDenied('Cashier payment permission is required.')


def paise(amount):
    return int(amount * Decimal('100'))


def get_or_create_order(sale, user):
    """Caller holds tenant and sale locks in a transaction."""
    if not checkout_available(sale.tenant):
        raise PaymentError('Enable online payments and save verified Razorpay credentials first.')
    existing = SalePaymentOrder.objects.filter(tenant=sale.tenant, sale=sale).first()
    if existing:
        return existing
    if sale.is_return or sale.order_status != Sale.ORDER_STATUS_OPEN or sale.paid_amount != 0 or sale.total < Decimal('1.00'):
        raise PaymentError('Razorpay requires an unpaid open bill of at least INR 1.00.')
    gateway = TenantPaymentGateway.objects.get(tenant=sale.tenant)
    result = gateway_request(gateway.key_id, decrypt_secret(gateway), 'POST', 'orders', {
        'amount': paise(sale.total), 'currency': 'INR', 'receipt': f'bill_{sale.tenant_id}_{sale.pk}',
        'notes': {'tenant_id': str(sale.tenant_id), 'sale_id': str(sale.pk)},
    })
    order_id = result.get('id', '')
    if (not isinstance(order_id, str) or not re.fullmatch(r'order_[A-Za-z0-9]{1,80}', order_id)
            or result.get('amount') != paise(sale.total) or result.get('currency') != 'INR'):
        raise PaymentError('Razorpay returned inconsistent order details. Payment was not started.')
    return SalePaymentOrder.objects.create(tenant=sale.tenant, sale=sale, key_id=gateway.key_id,
        gateway_order_id=order_id, amount=sale.total, created_by=user)


def order_gateway(order):
    gateway = TenantPaymentGateway.objects.filter(tenant=order.tenant, key_id=order.key_id).first()
    if gateway is None:
        raise PaymentError('The original merchant credentials are needed to verify this order.')
    return gateway


@sensitive_variables('secret')
def verify_signature(order, payment_id, signature):
    if (not re.fullmatch(r'pay_[A-Za-z0-9]{1,80}', payment_id)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', signature)):
        raise PaymentError('Invalid payment verification details.')
    secret = decrypt_secret(order_gateway(order))
    expected = hmac.new(secret.encode(), f'{order.gateway_order_id}|{payment_id}'.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise PaymentError('Payment signature verification failed. The bill has not been marked paid.')


def settle_payment(order, sale, payment):
    """Only called with trusted API data and tenant/sale/order row locks held."""
    if order.status == 'paid':
        return
    if (sale.is_return or sale.order_status != Sale.ORDER_STATUS_OPEN or sale.paid_amount != 0
            or sale.total != order.amount):
        raise PaymentError('Bill details changed. Contact the administrator to reconcile this payment.')
    if (payment.get('order_id') != order.gateway_order_id or payment.get('amount') != paise(order.amount)
            or payment.get('currency') != order.currency or payment.get('amount_refunded', 0) != 0
            or not isinstance(payment.get('id'), str)
            or not re.fullmatch(r'pay_[A-Za-z0-9]{1,80}', payment.get('id', ''))):
        raise PaymentError('Payment order, amount or currency did not match this bill.')
    gateway = order_gateway(order)
    expected_payment_id = payment['id']
    if payment.get('status') == 'authorized':
        payment = gateway_request(gateway.key_id, decrypt_secret(gateway), 'POST',
            f"payments/{payment['id']}/capture", {'amount': paise(order.amount), 'currency': order.currency})
    if (payment.get('id') != expected_payment_id or payment.get('status') != 'captured' or payment.get('captured') is not True
            or payment.get('order_id') != order.gateway_order_id
            or payment.get('amount') != paise(order.amount) or payment.get('currency') != order.currency
            or payment.get('amount_refunded', 0) != 0
            or not isinstance(payment.get('id'), str)
            or not re.fullmatch(r'pay_[A-Za-z0-9]{1,80}', payment.get('id', ''))):
        raise PaymentError('Payment is not captured yet. Use Check payment status to try again.')
    if SalePaymentOrder.objects.filter(payment_id=payment['id']).exclude(pk=order.pk).exists():
        raise PaymentError('This payment is already linked to another bill.')
    sale.order_status = Sale.ORDER_STATUS_PAID
    sale.paid_amount = order.amount
    sale.payment_method = 'other'
    sale.save(update_fields=['order_status', 'paid_amount', 'payment_method', 'updated_at'])
    # Reuse the existing billing side effects, once, after verified capture.
    from .views import apply_ingredient_deductions, _post_ledger_for_sale, _set_restaurant_table_status
    apply_ingredient_deductions(sale)
    CustomerLedger.objects.filter(tenant=sale.tenant, sale=sale).delete()
    _post_ledger_for_sale(sale)
    if sale.restaurant_table_id:
        _set_restaurant_table_status(sale.restaurant_table, RestaurantTable.STATUS_AVAILABLE)
    order.status = 'paid'
    order.payment_id = payment['id']
    order.paid_at = timezone.now()
    order.save(update_fields=['status', 'payment_id', 'paid_at', 'updated_at'])