import hashlib
import hmac
from decimal import Decimal
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth.models import Group, Permission
from django.test import TransactionTestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from posapp import views, payments
from posapp.payments import gateway_request as actual_gateway_request
import requests
from posapp.models import (Tenant, TenantMembership, TenantFeature, TenantPaymentGateway,
    Sale, SalePaymentOrder, IngredientStockMove, RestaurantTable)
from posapp.test_ingredient_deductions import IngredientDeductionTests


@override_settings(PAYMENT_CREDENTIAL_KEY=Fernet.generate_key().decode())
class RazorpayTests(TransactionTestCase):
    def setUp(self):
        IngredientDeductionTests.setUp(self)
        TenantFeature.objects.filter(tenant=self.tenant).update(online_payment=True)
        TenantMembership.objects.create(tenant=self.tenant, user=self.user, role='owner')
        self.client.force_login(self.user)
        session = self.client.session; session['active_tenant_id'] = self.tenant.pk; session.save()
        self.secret = 'fictional_secret_123456'
        self.gateway = TenantPaymentGateway.objects.create(tenant=self.tenant,
            key_id='rzp_test_Example123456', encrypted_secret=payments.encrypt_secret(self.secret),
            enabled=True, verified_at=timezone.now())
        self.module = reverse('restaurant_module_page', args=['online-payment'])
        a = patch('posapp.payments.gateway_request'); b = patch('posapp.payment_views.gateway_request')
        self.remote = a.start(); self.lookup = b.start()
        self.addCleanup(a.stop); self.addCleanup(b.stop)
        self.remote.return_value = {'id': 'order_Example123', 'amount': 10000, 'currency': 'INR'}
        self.lookup.return_value = {'items': []}

    def new_sale(self):
        request = IngredientDeductionTests.request(self, [IngredientDeductionTests.item(self)], action='razorpay')
        self.assertEqual(views.pos_sale_create(request).status_code, 302)
        return Sale.objects.filter(tenant=self.tenant).latest('pk')

    def new_order(self):
        sale = self.new_sale()
        response = self.client.post(reverse('razorpay_order', args=[sale.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        return sale, SalePaymentOrder.objects.get(sale=sale)

    def callback(self, order):
        return {'razorpay_order_id': order.gateway_order_id, 'razorpay_payment_id': 'pay_Example123',
            'razorpay_signature': hmac.new(self.secret.encode(),
                f'{order.gateway_order_id}|pay_Example123'.encode(), hashlib.sha256).hexdigest()}

    def captured(self, order):
        return {'id': 'pay_Example123', 'order_id': order.gateway_order_id,
                'amount': 10000, 'currency': 'INR', 'status': 'captured',
                'captured': True, 'amount_refunded': 0}

    def test_credentials_validation_encryption_and_blank_preserves_secret(self):
        self.assertEqual(self.client.post(self.module, {'key_id': self.gateway.key_id,
            'key_secret': self.secret, 'enabled': 'on'}).status_code, 302)
        self.gateway.refresh_from_db()
        self.assertNotIn(self.secret, self.gateway.encrypted_secret)
        self.assertEqual(payments.decrypt_secret(self.gateway), self.secret)
        self.lookup.assert_called_once_with(self.gateway.key_id, self.secret, 'GET', 'orders?count=1')
        self.assertNotContains(self.client.get(self.module), self.secret)
        self.assertEqual(self.client.post(self.module, {'key_id': self.gateway.key_id,
            'key_secret': '', 'enabled': 'on'}).status_code, 302)

    def test_invalid_keys_preserve_existing_configuration(self):
        self.lookup.side_effect = payments.PaymentError('Razorpay rejected the keys.')
        old = self.gateway.encrypted_secret
        response = self.client.post(self.module, {'key_id': 'rzp_live_Changed12345',
            'key_secret': 'replacement_secret', 'enabled': 'on'})
        self.assertContains(response, 'Razorpay rejected')
        self.assertNotContains(response, 'replacement_secret')
        self.gateway.refresh_from_db(); self.assertEqual(self.gateway.encrypted_secret, old)
        self.lookup.reset_mock()
        for key, secret in [('bad', 'long_secret'), ('rzp_test_Changed1234', ''), ('rzp_test_Changed1234', 'bad secret')]:
            self.assertEqual(self.client.post(self.module, {'key_id': key, 'key_secret': secret}).status_code, 200)
        self.lookup.assert_not_called()

    def test_pending_order_blocks_key_rotation_and_bill_edit(self):
        sale, order = self.new_order()
        response = self.client.post(self.module, {'key_id': 'rzp_live_Changed12345', 'key_secret': 'replacement_secret'})
        self.assertContains(response, 'Resolve pending payments')
        self.lookup.assert_not_called()
        response = self.client.post(reverse('sale_update', args=[sale.pk]), {'discount': 90})
        self.assertRedirects(response, reverse('payment_checkout', args=[sale.pk]), fetch_redirect_response=False)
        sale.refresh_from_db(); self.assertEqual(sale.total, Decimal('100'))

    def test_open_order_and_idempotent_creation(self):
        sale, order = self.new_order()
        self.assertEqual((sale.order_status, sale.paid_amount), ('open', 0))
        self.assertFalse(IngredientStockMove.objects.exists())
        self.assertEqual(self.client.post(reverse('razorpay_order', args=[sale.pk])).status_code, 200)
        self.assertEqual(SalePaymentOrder.objects.count(), 1)
        self.assertEqual(self.remote.call_count, 1)
        self.assertEqual(self.remote.call_args.args[4]['amount'], 10000)
        self.assertNotContains(self.client.get(reverse('payment_checkout', args=[sale.pk])), self.secret)

    def test_capture_settles_once_and_releases_table(self):
        sale, order = self.new_order()
        table = RestaurantTable.objects.create(tenant=self.tenant, name='T1', status='occupied')
        sale.restaurant_table = table; sale.save(update_fields=['restaurant_table'])
        self.lookup.return_value = self.captured(order)
        url = reverse('razorpay_verify', args=[sale.pk])
        response = self.client.post(url, self.callback(order))
        self.assertEqual(response.status_code, 200, response.content)
        sale.refresh_from_db(); order.refresh_from_db(); self.rice.refresh_from_db(); table.refresh_from_db()
        self.assertEqual((sale.order_status, sale.paid_amount, order.status), ('paid', Decimal('100'), 'paid'))
        self.assertEqual(self.rice.current_stock, Decimal('8'))
        self.assertEqual(table.status, 'available')
        self.assertEqual(IngredientStockMove.objects.count(), 2)
        self.assertEqual(self.client.post(url, self.callback(order)).status_code, 200)
        self.rice.refresh_from_db(); self.assertEqual(self.rice.current_stock, Decimal('8'))
        self.assertEqual(IngredientStockMove.objects.count(), 2)
        self.assertEqual(self.lookup.call_count, 1)

    def test_bad_signature_does_not_fetch_or_settle(self):
        sale, order = self.new_order()
        data = self.callback(order); data['razorpay_signature'] = '0' * 64
        self.assertEqual(self.client.post(reverse('razorpay_verify', args=[sale.pk]), data).status_code, 400)
        self.lookup.assert_not_called()
        sale.refresh_from_db(); self.assertEqual(sale.order_status, 'open')
        self.assertFalse(IngredientStockMove.objects.exists())

    def test_inconsistent_or_uncaptured_payment_rejected(self):
        sale, order = self.new_order()
        for change in [{'amount': 1}, {'currency': 'USD'}, {'order_id': 'order_Wrong'},
                       {'amount_refunded': 100}, {'status': 'failed'}, {'captured': False}, {'id': 'pay_Wrong'}]:
            with self.subTest(change=change):
                self.lookup.return_value = {**self.captured(order), **change}
                self.assertEqual(self.client.post(reverse('razorpay_verify', args=[sale.pk]), self.callback(order)).status_code, 400)
                sale.refresh_from_db(); self.assertEqual(sale.order_status, 'open')
        self.assertFalse(IngredientStockMove.objects.exists())

    def test_authorized_payment_capture(self):
        sale, order = self.new_order()
        self.lookup.return_value = {**self.captured(order), 'status': 'authorized', 'captured': False}
        self.remote.return_value = self.captured(order)
        self.assertEqual(self.client.post(reverse('razorpay_verify', args=[sale.pk]), self.callback(order)).status_code, 200)
        self.assertEqual(self.remote.call_args.args[2:4], ('POST', 'payments/pay_Example123/capture'))
        sale.refresh_from_db(); self.assertEqual(sale.order_status, 'paid')

    def test_recovery_after_timeout_and_disabled_checkout(self):
        sale, order = self.new_order()
        self.lookup.side_effect = payments.PaymentError('Razorpay is unavailable.')
        self.assertEqual(self.client.post(reverse('razorpay_verify', args=[sale.pk]), self.callback(order)).status_code, 400)
        sale.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((sale.order_status, order.status), ('open', 'created'))
        self.gateway.enabled = False; self.gateway.save()
        self.assertEqual(self.client.post(reverse('razorpay_order', args=[sale.pk])).status_code, 400)
        self.lookup.side_effect = None; self.lookup.return_value = {'items': [self.captured(order)]}
        self.assertEqual(self.client.post(reverse('razorpay_check', args=[sale.pk])).status_code, 200)
        sale.refresh_from_db(); self.assertEqual(sale.order_status, 'paid')

    def test_cross_tenant_access_denied(self):
        sale, order = self.new_order()
        other = Tenant.objects.create(name='Other', slug='payment-other', business_type='restaurant')
        TenantMembership.objects.create(tenant=other, user=self.user)
        session = self.client.session; session['active_tenant_id'] = other.pk; session.save()
        for route in ['razorpay_order', 'razorpay_verify', 'razorpay_check']:
            self.assertEqual(self.client.post(reverse(route, args=[sale.pk]), self.callback(order)).status_code, 404)
        self.lookup.assert_not_called()

    def test_waiter_and_cashier_permissions(self):
        sale = self.new_sale()
        group = Group.objects.create(name='Waiter'); self.user.groups.add(group)
        self.assertEqual(self.client.post(reverse('razorpay_order', args=[sale.pk])).status_code, 403)
        self.user.groups.clear(); self.user.is_superuser = False; self.user.save()
        self.user.user_permissions.add(Permission.objects.get(codename='can_pos', content_type__model='apppermission'))
        self.assertEqual(self.client.get(self.module).status_code, 200)
        self.assertEqual(self.client.post(self.module, {'key_id': self.gateway.key_id}).status_code, 403)

    def test_csrf_and_post_required(self):
        sale = self.new_sale(); url = reverse('razorpay_order', args=[sale.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.user)
        self.assertEqual(strict.post(url).status_code, 403)

    def test_paid_return_or_part_paid_bill_rejected(self):
        for field, value in [('order_status', 'paid'), ('is_return', True), ('paid_amount', Decimal('1'))]:
            sale = self.new_sale(); setattr(sale, field, value); sale.save()
            self.assertEqual(self.client.post(reverse('razorpay_order', args=[sale.pk])).status_code, 400)
        self.remote.assert_not_called()

    def test_bad_encryption_key_controlled_error(self):
        with override_settings(PAYMENT_CREDENTIAL_KEY=''):
            with self.assertRaises(payments.PaymentError): payments.decrypt_secret(self.gateway)

    def test_api_client_auth_timeout_and_error_redaction(self):
        with patch('posapp.payments.requests.request') as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {'items': []}
            self.assertEqual(actual_gateway_request(self.gateway.key_id, self.secret, 'GET', 'orders?count=1'), {'items': []})
            self.assertEqual(request.call_args.kwargs['auth'], (self.gateway.key_id, self.secret))
            self.assertFalse(request.call_args.kwargs['allow_redirects'])
            request.return_value.status_code = 401
            with self.assertRaises(payments.PaymentError) as error:
                actual_gateway_request(self.gateway.key_id, self.secret, 'GET', 'orders?count=1')
            self.assertNotIn(self.secret, str(error.exception))
            request.side_effect = requests.Timeout(self.secret)
            with self.assertRaises(payments.PaymentError) as error:
                actual_gateway_request(self.gateway.key_id, self.secret, 'GET', 'orders?count=1')
            self.assertNotIn(self.secret, str(error.exception))

    def test_invalid_remote_order_response_does_not_create_local_order(self):
        sale = self.new_sale()
        for malformed in [{'id': None, 'amount': 10000, 'currency': 'INR'},
                          {'id': 'order_Example123', 'amount': 1, 'currency': 'INR'}]:
            self.remote.return_value = malformed
            self.assertEqual(self.client.post(reverse('razorpay_order', args=[sale.pk])).status_code, 400)
        self.assertFalse(SalePaymentOrder.objects.exists())


    def test_failed_website_mismatch_reports_reason_without_settlement(self):
        sale, order = self.new_order()
        self.lookup.return_value = {'items': [{**self.captured(order), 'status': 'failed',
            'captured': False, 'error_description': 'Payment blocked as website does not match registered website(s)'}]}
        response = self.client.post(reverse('razorpay_check', args=[sale.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertIn('registered website', response.json()['error'])
        sale.refresh_from_db()
        self.assertEqual(sale.order_status, 'open')
        self.assertFalse(IngredientStockMove.objects.exists())
        self.gateway.key_id = 'rzp_live_Example123456'
        self.gateway.save(update_fields=['key_id'])
        self.assertContains(self.client.get(reverse('payment_checkout', args=[sale.pk])), 'Live mode:')


    def test_verified_payment_opens_printable_invoice_only_after_capture(self):
        sale, order = self.new_order()
        invoice = reverse('invoice_view', args=[sale.pk])
        self.assertNotContains(self.client.get(invoice + '?print=1'), 'invoice-print.js')
        self.lookup.return_value = self.captured(order)
        response = self.client.post(reverse('razorpay_verify', args=[sale.pk]), self.callback(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], invoice + '?print=1')
        page = self.client.get(response.json()['redirect'])
        self.assertContains(page, 'invoice-print.js')
        self.assertContains(page, 'Payment verified via Razorpay')
        self.assertContains(page, 'pay_Example123')
        self.assertNotContains(self.client.get(invoice), 'invoice-print.js')

    def test_recovered_payment_also_opens_printable_invoice(self):
        sale, order = self.new_order()
        self.lookup.return_value = {'items': [self.captured(order)]}
        response = self.client.post(reverse('razorpay_check', args=[sale.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(response.json()['redirect']), 'invoice-print.js')
