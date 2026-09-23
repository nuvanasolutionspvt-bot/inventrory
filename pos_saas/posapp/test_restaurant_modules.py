from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import RESTAURANT_MODULE_CHOICES, SaleForm, TenantRegistrationForm
from .models import Tenant, TenantFeature, TenantMembership


class RestaurantModuleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('module-admin', 'modules@example.com', 'test-password')
        self.tenant = Tenant.objects.create(name='Module test', slug='module-test', business_type='restaurant')
        self.features = TenantFeature.objects.create(tenant=self.tenant, products_catalog=False)
        TenantMembership.objects.create(tenant=self.tenant, user=self.user, role='owner')
        self.client.force_login(self.user)
        self.activate(self.tenant)

    def activate(self, tenant):
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()

    def test_disabled_links_and_direct_urls(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        routes = [('product_list', []), ('product_create', []), ('restaurant_catalog_setup', []),
                  ('kitchen_orders', []), ('kitchen_display', []), ('restaurant_tables', []),
                  ('restaurant_module_page', ['inventory']), ('restaurant_module_page', ['online-payment'])]
        for name, args in routes:
            with self.subTest(name=name, args=args):
                url = reverse(name, args=args)
                self.assertNotContains(response, 'href="' + url + '"')
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertContains(response, 'href="' + reverse('pos_sale_create') + '"')

    def test_selected_module_matrix(self):
        routes = {'products_catalog': ('product_list', []), 'kot_management': ('kitchen_orders', []),
                  'table_management': ('restaurant_tables', []), 'kitchen_display': ('kitchen_display', []),
                  'inventory': ('restaurant_module_page', ['inventory']),
                  'online_payment': ('restaurant_module_page', ['online-payment'])}
        for selected, (name, args) in routes.items():
            with self.subTest(selected=selected):
                TenantFeature.objects.filter(pk=self.features.pk).update(**{key: key == selected for key in routes})
                response = self.client.get(reverse('dashboard'))
                for key, (route, route_args) in routes.items():
                    url = reverse(route, args=route_args)
                    if key == selected:
                        self.assertContains(response, 'href="' + url + '"')
                        self.assertEqual(self.client.get(url).status_code, 200)
                    else:
                        self.assertNotContains(response, 'href="' + url + '"')
                        self.assertEqual(self.client.get(url).status_code, 403)

    def test_tenant_switch_refreshes_modules(self):
        other = Tenant.objects.create(name='Other', slug='other-modules', business_type='restaurant')
        TenantFeature.objects.create(tenant=other, kitchen_display=True)
        TenantMembership.objects.create(tenant=other, user=self.user, role='owner')
        for tenant, visible in [(other, True), (self.tenant, False), (other, True)]:
            self.activate(tenant)
            response = self.client.get(reverse('dashboard'))
            self.assertEqual(('href="' + reverse('kitchen_display') + '"') in response.content.decode(), visible)

    def test_disabled_pos_and_table_field(self):
        self.features.pos_billing = False
        self.features.save()
        self.assertEqual(self.client.get(reverse('pos_sale_create')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'href="' + reverse('pos_sale_create') + '"')
        form = SaleForm(tenant=self.tenant)
        self.assertTrue(form.fields['restaurant_table'].disabled)
        self.assertFalse(form.fields['restaurant_table'].queryset.exists())

    def test_retail_is_not_restricted_by_restaurant_flags(self):
        self.tenant.business_type = 'retail'
        self.tenant.save()
        self.assertEqual(self.client.get(reverse('product_list')).status_code, 200)

    def registration_data(self, modules):
        return dict(business_name='New Restaurant', account_slug='new-restaurant', business_type='restaurant',
                    business_modules=modules, subscription_plan='trial', owner_name='Owner',
                    contact_email='new@example.com', contact_phone='9876543210', address='Test street',
                    city='Pune', state='Maharashtra', postal_code='411001', country='India',
                    username='new-owner', password1='Testing!2837modules', password2='Testing!2837modules', accepted_terms='on')

    def test_registration_saves_exact_flags_and_redirect(self):
        self.client.logout()
        response = self.client.post(reverse('register'), self.registration_data(['pos_billing', 'table_management']))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        features = TenantFeature.objects.get(tenant__slug='new-restaurant')
        for key, _ in RESTAURANT_MODULE_CHOICES:
            self.assertEqual(getattr(features, key), key in ['pos_billing', 'table_management'])

    def test_registration_with_catalog_redirects_to_setup(self):
        self.client.logout()
        response = self.client.post(reverse('register'), self.registration_data(['pos_billing', 'products_catalog']))
        self.assertRedirects(response, reverse('restaurant_catalog_setup'), fetch_redirect_response=False)

    def test_registration_rejects_missing_pos_and_unknown_modules(self):
        for modules in [[], ['pos_billing', 'unknown']]:
            form = TenantRegistrationForm(self.registration_data(modules))
            self.assertFalse(form.is_valid())
            self.assertIn('business_modules', form.errors)

    def test_table_query_cannot_bypass_disabled_module(self):
        self.assertEqual(self.client.get(reverse('pos_sale_create'), {'table': '1'}).status_code, 403)

    def test_enabled_modules_still_respect_staff_permissions(self):
        TenantFeature.objects.filter(pk=self.features.pk).update(kot_management=True, kitchen_display=True,
                                                                 table_management=True, inventory=True, online_payment=True)
        staff = get_user_model().objects.create_user('module-staff')
        TenantMembership.objects.create(tenant=self.tenant, user=staff, role='staff')
        self.client.force_login(staff)
        self.activate(self.tenant)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        for name in ['kitchen_orders', 'kitchen_display', 'restaurant_tables']:
            self.assertNotContains(response, 'href="' + reverse(name) + '"')
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_regular_admins_have_access_to_selected_modules(self):
        self.user.is_superuser = False
        self.user.is_staff = False
        self.user.save()
        for role in ['owner', 'admin']:
            with self.subTest(role=role):
                TenantMembership.objects.filter(user=self.user, tenant=self.tenant).update(role=role)
                self.test_selected_module_matrix()

    def test_admin_access_does_not_follow_user_to_staff_store(self):
        self.user.is_superuser = False
        self.user.save()
        other = Tenant.objects.create(name='Staff store', slug='staff-store', business_type='restaurant')
        TenantFeature.objects.create(tenant=other, kitchen_display=True)
        TenantMembership.objects.create(tenant=other, user=self.user, role='staff')
        self.activate(other)
        self.assertEqual(self.client.get(reverse('kitchen_display')).status_code, 403)
