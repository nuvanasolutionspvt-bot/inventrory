from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TransactionTestCase
from django.urls import reverse

from posapp import views
from posapp.forms import IngredientForm
from posapp.models import (Ingredient, IngredientPurchase, IngredientStockMove, Product,
                          Recipe, RecipeIngredient, Tenant, TenantFeature, TenantMembership)
from posapp.test_ingredient_deductions import IngredientDeductionTests


class RestaurantInventoryUITests(TransactionTestCase):
    def setUp(self):
        IngredientDeductionTests.setUp(self)
        TenantMembership.objects.create(tenant=self.tenant, user=self.user, role='owner')
        self.client.force_login(self.user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()
        self.entry = reverse('restaurant_module_page', args=['inventory'])
        self.edit = reverse('product_update', args=[self.product.pk])

    def product_data(self, full='3.00', half='1.50'):
        data = {'name': self.product.name, 'unit_price': '100.00', 'half_price': '60.00',
                'full_available': 'on', 'cost_price': '7.00', 'tax_percent': '0.00',
                'reorder_level': '0', 'is_active': 'on'}
        for portion, qty in [('full', full), ('half', half)]:
            data.update({f'{portion}-TOTAL_FORMS': '1', f'{portion}-INITIAL_FORMS': '0',
                         f'{portion}-0-ingredient': str(self.rice.pk),
                         f'{portion}-0-quantity_required': qty})
        return data

    def test_inventory_entry_forms_recipe_page_and_dashboard_render(self):
        for url in [self.entry, reverse('ingredient_create'), reverse('ingredient_purchase'),
                    reverse('ingredient_edit', args=[self.rice.pk]), self.edit, reverse('dashboard')]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get(self.edit)
        self.assertContains(response, 'Recipes &amp; Food Cost')
        self.assertContains(response, 'recipe-ingredient-costs')
        self.assertContains(self.client.get(self.entry), 'Rice')

    def test_food_cost_and_percentage_keep_manual_cost(self):
        self.product.cost_price = Decimal('7.00')
        self.product.save(update_fields=['cost_price'])
        self.assertEqual(self.product.get_recipe_food_cost('full'), Decimal('60.00'))
        self.assertEqual(self.product.get_recipe_food_cost('half'), Decimal('30.00'))
        self.assertEqual(self.product.get_recipe_food_cost_percentage('full'), Decimal('60.00'))
        self.assertEqual(self.product.get_recipe_food_cost_percentage('half'), Decimal('50.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost_price, Decimal('7.00'))
        self.product.unit_price = Decimal('0.00')
        self.assertIsNone(self.product.get_recipe_food_cost_percentage('full'))
        self.product.half_price = None
        self.assertIsNone(self.product.get_recipe_food_cost_percentage('half'))
        Recipe.objects.filter(product=self.product, portion='half').delete()
        self.assertIsNone(self.product.get_recipe_food_cost('half'))

    def test_create_ingredient_and_receive_stock_through_forms(self):
        response = self.client.post(reverse('ingredient_create'), {
            'name': 'Flour', 'unit': 'kg', 'current_stock': '2.50',
            'low_stock_threshold': '1.00', 'cost_per_unit': '15.00', 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        ingredient = Ingredient.objects.get(tenant=self.tenant, name='Flour')
        response = self.client.post(reverse('ingredient_purchase'), {
            'ingredient': ingredient.pk, 'quantity': '3.25', 'cost_per_unit': '18.00',
            'supplier_name': 'Test supplier',
        })
        self.assertEqual(response.status_code, 302)
        ingredient.refresh_from_db()
        self.assertEqual(ingredient.current_stock, Decimal('5.75'))
        self.assertEqual(ingredient.cost_per_unit, Decimal('18.00'))
        self.assertEqual(ingredient.purchases.get().created_by, self.user)

    def test_recipe_builder_saves_both_portions_and_removes_rows(self):
        response = self.client.post(self.edit, self.product_data())
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.get_recipe_food_cost('full'), Decimal('60.00'))
        self.assertEqual(self.product.get_recipe_food_cost('half'), Decimal('30.00'))
        self.assertEqual(self.product.cost_price, Decimal('7.00'))
        self.assertEqual(RecipeIngredient.objects.filter(recipe__product=self.product).count(), 2)
        data = self.product_data()
        data['full-0-DELETE'] = 'on'
        self.assertEqual(self.client.post(self.edit, data).status_code, 302)
        self.assertIsNone(self.product.get_recipe_food_cost('full'))
        self.assertIsNotNone(self.product.get_recipe_food_cost('half'))

    def test_invalid_recipe_does_not_save_menu_changes(self):
        data = self.product_data(full='-1.00')
        data['name'] = 'Should not save'
        response = self.client.post(self.edit, data)
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Dish')
        self.assertEqual(self.product.get_recipe_food_cost('full'), Decimal('60.00'))
        data = self.product_data()
        data.update({'full-TOTAL_FORMS': '2', 'full-1-ingredient': str(self.rice.pk),
                     'full-1-quantity_required': '1.00'})
        self.assertContains(self.client.post(self.edit, data), 'Use each ingredient only once')

    def test_cross_tenant_ingredient_and_recipe_submission_rejected(self):
        other = Tenant.objects.create(name='Other', slug='other-ingredients', business_type='restaurant')
        ingredient = Ingredient.objects.create(tenant=other, name='Private spice', unit='g', current_stock=10)
        self.assertNotContains(self.client.get(self.entry), 'Private spice')
        self.assertEqual(self.client.get(reverse('ingredient_edit', args=[ingredient.pk])).status_code, 404)
        response = self.client.post(reverse('ingredient_purchase'), {
            'ingredient': ingredient.pk, 'quantity': '1.00', 'cost_per_unit': '2.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IngredientPurchase.objects.exists())
        data = self.product_data()
        data['full-0-ingredient'] = str(ingredient.pk)
        self.assertEqual(self.client.post(self.edit, data).status_code, 200)
        self.assertFalse(RecipeIngredient.objects.filter(ingredient=ingredient).exists())

    def test_disabled_feature_blocks_ui_and_deduction(self):
        TenantFeature.objects.filter(tenant=self.tenant).update(inventory=False)
        self.assertEqual(self.client.get(self.entry).status_code, 403)
        self.assertEqual(self.client.get(reverse('ingredient_create')).status_code, 403)
        self.assertNotContains(self.client.get(self.edit), 'recipe-builder')
        request = IngredientDeductionTests.request(self, [IngredientDeductionTests.item(self)])
        self.assertEqual(views.pos_sale_create(request).status_code, 302)
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal('10.00'))
        self.assertFalse(IngredientStockMove.objects.exists())

    def test_menu_staff_can_manage_without_django_admin_flag(self):
        self.user.is_superuser = False
        self.user.is_staff = False
        self.user.save()
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='posapp', codename='change_product'))
        self.assertContains(self.client.get(self.entry), 'Ingredient Inventory')
        self.assertContains(self.client.get(self.edit), 'recipe-builder')

    def test_cashier_cannot_access_inventory_or_menu_costs(self):
        self.user.is_superuser = False
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get(self.entry).status_code, 403)
        self.assertEqual(self.client.get(self.edit).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Low Ingredient Stock')

    def test_low_stock_card_and_warning_badges(self):
        self.rice.current_stock = Decimal('0.00')
        self.rice.low_stock_threshold = Decimal('1.00')
        self.rice.save()
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Low Ingredient Stock')
        self.assertEqual(response.context['low_ingredient_count'], 1)
        self.assertContains(response, 'text-bg-danger')
        self.assertContains(self.client.get(self.entry + '?low=1'), 'Rice')
        self.assertNotContains(self.client.get(self.entry + '?low=1'), '>Oil<')

    def test_edit_cannot_overwrite_stock_or_change_used_unit(self):
        response = self.client.post(reverse('ingredient_edit', args=[self.rice.pk]), {
            'name': 'Rice', 'unit': 'g', 'current_stock': '999.00',
            'cost_per_unit': '20.00', 'low_stock_threshold': '1.00', 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal('10.00'))
        self.assertEqual(self.rice.unit, 'kg')

    def test_admin_registration_is_tenant_scoped_and_history_readonly(self):
        other = Tenant.objects.create(name='Other', slug='admin-other', business_type='restaurant')
        Ingredient.objects.create(tenant=other, name='Hidden', unit='kg')
        self.user.is_superuser = False
        self.user.save()
        request = self.factory.get('/admin/')
        request.user = self.user
        for model in [Ingredient, IngredientPurchase, Recipe, RecipeIngredient, IngredientStockMove]:
            model_admin = admin.site._registry[model]
            self.assertIn('tenant', model_admin.list_display)
            self.assertFalse(model_admin.has_delete_permission(request))
            self.assertFalse(model_admin.get_queryset(request).exclude(tenant=self.tenant).exists())

    def test_ui_recipe_drives_billing_and_purchase_recosts(self):
        self.assertEqual(self.client.post(self.edit, self.product_data()).status_code, 302)
        request = IngredientDeductionTests.request(self, [IngredientDeductionTests.item(self, qty=2)])
        self.assertEqual(views.pos_sale_create(request).status_code, 302)
        self.rice.refresh_from_db()
        self.oil.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal('4.00'))
        self.assertEqual(self.oil.current_stock, Decimal('5.00'))
        self.assertEqual(IngredientStockMove.objects.count(), 1)
        self.assertEqual(self.client.post(reverse('ingredient_purchase'), {
            'ingredient': self.rice.pk, 'quantity': '2.00', 'cost_per_unit': '30.00',
        }).status_code, 302)
        self.product.refresh_from_db()
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal('6.00'))
        self.assertEqual(self.product.get_recipe_food_cost('full'), Decimal('90.00'))
        self.assertEqual(self.product.get_recipe_food_cost_percentage('half'), Decimal('75.00'))
        self.assertEqual(self.product.cost_price, Decimal('7.00'))
