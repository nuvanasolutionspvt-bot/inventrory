from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Tenant, TenantMembership


class UserCreationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('user-test-admin', 'admin@example.com', 'test-password')
        self.tenant = Tenant.objects.create(name='Users test', slug='users-test', business_type='restaurant')
        TenantMembership.objects.create(tenant=self.tenant, user=self.owner, role='owner')
        self.client.force_login(self.owner)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()
        self.url = reverse('security_user_new')
        self.data = dict(username='new-staff', email='staff@example.com', is_active='on', is_staff='on',
                         password1='Staff!8327test', password2='Staff!8327test')

    def test_valid_user_created_linked_listed_and_can_authenticate(self):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse('security_users'))
        user = TenantMembership.objects.get(tenant=self.tenant, login_username='new-staff').user
        self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant, user=user, is_active=True).exists())
        self.assertEqual(authenticate(username=user.username, password=self.data['password1']), user)
        self.assertContains(self.client.get(reverse('security_users')), 'new-staff')

    def test_duplicate_username_error_is_visible(self):
        self.data['username'] = self.owner.username
        response = self.client.post(self.url, self.data)
        self.assertContains(response, 'A user with that username already exists in this business.')
        self.assertContains(response, 'User was not saved.')
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_password_mismatch_error_is_visible(self):
        self.data['password2'] = 'different'
        response = self.client.post(self.url, self.data)
        self.assertContains(response, 'Passwords do not match.')
        self.assertFalse(TenantMembership.objects.filter(tenant=self.tenant, login_username='new-staff').exists())

    def test_invalid_role_error_is_visible(self):
        self.data['groups'] = ['999999']
        response = self.client.post(self.url, self.data)
        self.assertContains(response, 'Select a valid choice.')
        self.assertFalse(TenantMembership.objects.filter(tenant=self.tenant, login_username='new-staff').exists())

    def test_membership_failure_rolls_back_user(self):
        with patch('posapp.forms.TenantMembership.objects.create', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.data)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_edit_password_is_optional_and_errors_are_visible(self):
        response = self.client.get(reverse('security_user_edit', args=[self.owner.pk]))
        self.assertContains(response, 'New Password (optional)')
        response = self.client.post(reverse('security_user_edit', args=[self.owner.pk]),
                                    dict(email='admin@example.com', password1='one', password2='two'))
        self.assertContains(response, 'Passwords do not match.')

    def test_same_name_in_two_businesses_has_separate_credentials_and_access(self):
        other = Tenant.objects.create(name='Other', slug='other-user-business', business_type='restaurant')
        existing = get_user_model().objects.create_user('adnan', password='Other!password123')
        TenantMembership.objects.create(tenant=other, user=existing, role='staff')
        self.data['username'] = 'adnan'
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse('security_users'))
        member = TenantMembership.objects.get(tenant=self.tenant, login_username='adnan')
        self.assertNotEqual(member.user_id, existing.pk)
        self.assertFalse(member.user.tenant_memberships.filter(tenant=other).exists())
        self.assertContains(self.client.get(reverse('security_users')), 'adnan')
        self.client.logout()
        response = self.client.post(reverse('login'), dict(username='adnan', password=self.data['password1']))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), member.user_id)
        self.assertEqual(self.client.session['active_tenant_id'], self.tenant.pk)
        self.client.logout()
        response = self.client.post(reverse('login'), dict(username='adnan', password='wrong-password'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        response = self.client.post(reverse('login'), dict(username='adnan', password='Other!password123'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), existing.pk)

    def test_duplicate_within_tenant_case_insensitive(self):
        self.client.post(self.url, self.data)
        self.data['username'] = 'NEW-STAFF'
        response = self.client.post(self.url, self.data)
        self.assertContains(response, 'already exists in this business')
        self.assertEqual(self.tenant.memberships.count(), 2)

    def test_inactive_membership_cannot_log_in(self):
        self.client.post(self.url, self.data)
        self.tenant.memberships.filter(login_username='new-staff').update(is_active=False)
        self.client.logout()
        response = self.client.post(reverse('login'), dict(username='new-staff', password=self.data['password1']))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_has_no_account_field(self):
        self.client.logout()
        response = self.client.get(reverse('login'))
        self.assertNotContains(response, 'name="account_id"')
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')

    def test_identical_credentials_never_choose_another_business(self):
        self.client.post(self.url, self.data)
        other = Tenant.objects.create(name='Other', slug='ambiguous-business')
        user = get_user_model().objects.create_user('legacy-staff', password=self.data['password1'])
        TenantMembership.objects.create(tenant=other, user=user, login_username='new-staff')
        self.client.logout()
        response = self.client.post(reverse('login'), dict(username='new-staff', password=self.data['password1']))
        self.assertContains(response, 'These credentials match more than one business.')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_existing_admin_login_without_account(self):
        self.client.logout()
        response = self.client.post(reverse('login'), dict(username=self.owner.username, password='test-password'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.owner.pk)
