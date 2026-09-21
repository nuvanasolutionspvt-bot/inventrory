import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection, transaction
from django.db.models.sql.compiler import SQLCompiler
from django.test import RequestFactory, TransactionTestCase

from posapp import views
from posapp.models import (
    Ingredient, IngredientPurchase, IngredientStockMove, Product, Recipe,
    RecipeIngredient, Sale, SaleItem, StockMove, Tenant, TenantFeature,
)


class IngredientDeductionTests(TransactionTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create(
            username='inventory-test', is_staff=True, is_superuser=True,
        )
        self.tenant = Tenant.objects.create(name='Restaurant', slug='ingredient-test', business_type='restaurant')
        TenantFeature.objects.create(tenant=self.tenant, kot_management=True, inventory=True)
        self.product = Product.objects.create(
            tenant=self.tenant, code='DISH', name='Dish', unit_price=Decimal('100.00'),
            half_price=Decimal('60.00'),
        )
        self.rice = Ingredient.objects.create(
            tenant=self.tenant, name='Rice', unit='kg', current_stock=Decimal('10.00'), cost_per_unit=Decimal('20.00'),
        )
        self.oil = Ingredient.objects.create(
            tenant=self.tenant, name='Oil', unit='l', current_stock=Decimal('5.00'), cost_per_unit=Decimal('40.00'),
        )
        for portion, rice, oil in [('full', '2.00', '0.50'), ('half', '1.00', '0.25')]:
            recipe = Recipe.objects.create(tenant=self.tenant, product=self.product, portion=portion)
            for ingredient, qty in [(self.rice, rice), (self.oil, oil)]:
                RecipeIngredient.objects.create(
                    tenant=self.tenant, recipe=recipe, ingredient=ingredient, quantity_required=Decimal(qty),
                )

    def request(self, items, *, action='complete', is_return=False):
        data = {
            'date': date.today().isoformat(), 'discount': '0.00', 'paid_amount': '0.00',
            'payment_method': 'cash', 'sale_action': action, 'items_json': json.dumps(items),
        }
        if is_return:
            data['is_return'] = 'on'
        request = self.factory.post('/pos/', data)
        request.user = self.user
        request.tenant = self.tenant
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def item(self, portion='full', qty=1):
        return {'product_id': self.product.pk, 'portion': portion, 'qty': qty, 'unit_price': '100.00'}

    def create_sale(self, items=None, **kwargs):
        response = views.pos_sale_create(self.request(items or [self.item()], **kwargs))
        self.assertEqual(response.status_code, 302)
        return Sale.objects.filter(tenant=self.tenant).latest('pk')

    def snapshot(self, sale):
        self.rice.refresh_from_db()
        self.oil.refresh_from_db()
        return (self.rice.current_stock, self.oil.current_stock,
                IngredientStockMove.objects.filter(sale=sale).count())

    def test_purchase_increments_stock_and_cost_once(self):
        purchase = IngredientPurchase.objects.create(
            tenant=self.tenant, ingredient=self.rice, quantity=Decimal('3.25'),
            cost_per_unit=Decimal('27.50'), created_by=self.user,
        )
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal('13.25'))
        self.assertEqual(self.rice.cost_per_unit, Decimal('27.50'))
        purchase.supplier_name = 'Supplier'
        purchase.save()
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal('13.25'))

    def test_paid_full_and_half_multiple_ingredients(self):
        sale = self.create_sale([self.item('full', 2), self.item('half', 1)])
        self.assertEqual(sale.order_status, Sale.ORDER_STATUS_PAID)
        self.assertEqual(self.snapshot(sale), (Decimal('5.00'), Decimal('3.75'), 4))
        self.assertEqual(list(sale.saleitem_set.order_by('pk').values_list('details', flat=True)), ['Full', 'Half'])

    def test_generate_kot_never_calls_deduction_on_create_or_edit(self):
        with patch.object(views, 'apply_ingredient_deductions', wraps=views.apply_ingredient_deductions) as deduct:
            sale = self.create_sale(action='generate_kot')
            response = views.sale_update(self.request([self.item(qty=2)], action='generate_kot'), sale.pk)
            self.assertEqual(response.status_code, 302)
            deduct.assert_not_called()
        sale.refresh_from_db()
        self.assertEqual(sale.order_status, Sale.ORDER_STATUS_OPEN)
        self.assertEqual(self.snapshot(sale), (Decimal('10.00'), Decimal('5.00'), 0))

    def test_non_restaurant_never_calls_deduction_on_create_or_edit(self):
        self.tenant.business_type = 'retail_store'
        self.tenant.save(update_fields=['business_type'])
        StockMove.objects.create(tenant=self.tenant, product=self.product, change=20, reason='purchase')
        with patch.object(views, 'apply_ingredient_deductions', wraps=views.apply_ingredient_deductions) as deduct:
            sale = self.create_sale()
            response = views.sale_update(self.request([self.item(qty=2)]), sale.pk)
            self.assertEqual(response.status_code, 302)
            deduct.assert_not_called()
        self.assertEqual(sale.order_status, Sale.ORDER_STATUS_PAID)
        self.assertEqual(self.snapshot(sale), (Decimal('10.00'), Decimal('5.00'), 0))

    def test_edit_paid_sale_reverses_and_rebuilds(self):
        sale = self.create_sale([self.item(qty=2)])
        before = self.snapshot(sale)
        response = views.sale_update(self.request([self.item(qty=3)]), sale.pk)
        self.assertEqual(response.status_code, 302)
        after = self.snapshot(sale)
        self.assertEqual(before, (Decimal('6.00'), Decimal('4.00'), 2))
        self.assertEqual(after, (Decimal('4.00'), Decimal('3.50'), 2))
        response = views.sale_update(self.request([self.item(qty=3)]), sale.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(sale), after)
        print(f'EDIT AUDIT: before rice={before[0]} oil={before[1]} moves={before[2]}; '
              f'after rice={after[0]} oil={after[1]} moves={after[2]}; repeat={self.snapshot(sale)}')

    def test_clamp_logs_and_reversal_restores_only_actual_deduction(self):
        self.rice.current_stock = Decimal('1.00')
        self.rice.save(update_fields=['current_stock'])
        with self.assertLogs('posapp.views', level='WARNING') as logs:
            sale = self.create_sale()
        self.assertTrue(any('clamped to zero' in entry for entry in logs.output))
        move = IngredientStockMove.objects.get(sale=sale, ingredient=self.rice)
        self.assertEqual(move.quantity_deducted, Decimal('1.00'))
        self.assertEqual(self.snapshot(sale), (Decimal('0.00'), Decimal('4.50'), 2))
        with self.assertLogs('posapp.views', level='WARNING'):
            response = views.sale_update(self.request([self.item()]), sale.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(sale), (Decimal('0.00'), Decimal('4.50'), 2))
        self.assertEqual(IngredientStockMove.objects.get(sale=sale, ingredient=self.rice).quantity_deducted, Decimal('1.00'))

    def test_missing_portion_recipe_logs_and_sale_completes(self):
        Recipe.objects.filter(product=self.product, portion='half').delete()
        with self.assertLogs('posapp.views', level='WARNING') as logs:
            sale = self.create_sale([self.item('half')])
        self.assertTrue(any('No ingredient recipe' in entry and 'portion=half' in entry for entry in logs.output))
        self.assertEqual(sale.order_status, Sale.ORDER_STATUS_PAID)
        self.assertEqual(self.snapshot(sale), (Decimal('10.00'), Decimal('5.00'), 0))

    def test_existing_portion_normalization_and_default(self):
        sale = self.create_sale(action='generate_kot')
        item = sale.saleitem_set.get()
        sale.order_status = Sale.ORDER_STATUS_PAID
        sale.save(update_fields=['order_status'])
        for details, expected in [('Half', '9.00'), ('half', '9.00'), (' HALF ', '9.00'),
                                  ('Full', '8.00'), ('', '8.00'), ('unknown', '8.00')]:
            with self.subTest(details=details):
                item.details = details
                item.save(update_fields=['details'])
                with transaction.atomic():
                    views.apply_ingredient_deductions(sale)
                self.assertEqual(self.snapshot(sale)[0], Decimal(expected))

    def test_returns_do_not_consume_and_converting_sale_reverses(self):
        returned = self.create_sale(is_return=True)
        self.assertTrue(returned.is_return)
        self.assertEqual(returned.saleitem_set.get().qty, -1)
        self.assertEqual(self.snapshot(returned), (Decimal('10.00'), Decimal('5.00'), 0))
        sale = self.create_sale()
        response = views.sale_update(self.request([self.item()], is_return=True), sale.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(sale), (Decimal('10.00'), Decimal('5.00'), 0))

    def test_settling_open_order_deducts_once(self):
        sale = self.create_sale(action='generate_kot')
        response = views.sale_update(self.request([self.item('half', 2)]), sale.pk)
        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.order_status, Sale.ORDER_STATUS_PAID)
        self.assertEqual(self.snapshot(sale), (Decimal('8.00'), Decimal('4.50'), 2))

    def test_row_lock_queries_evaluate_inside_callers_atomic_block(self):
        original_execute = SQLCompiler.execute_sql
        evaluations = []

        def record(compiler, *args, **kwargs):
            if compiler.query.select_for_update:
                evaluations.append((compiler.query.model, connection.in_atomic_block,
                                    len(connection.atomic_blocks)))
            return original_execute(compiler, *args, **kwargs)

        self.assertFalse(connection.in_atomic_block)
        with patch.object(SQLCompiler, 'execute_sql', record):
            sale = self.create_sale()
        self.assertTrue({Sale, Ingredient, IngredientStockMove}.issubset({row[0] for row in evaluations}))
        self.assertTrue(all(inside and depth == 1 for _, inside, depth in evaluations))
        evaluations.clear()
        with patch.object(SQLCompiler, 'execute_sql', record):
            response = views.sale_update(self.request([self.item(qty=2)]), sale.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(evaluations[0][0], Sale)
        self.assertTrue({Sale, Ingredient, IngredientStockMove}.issubset({row[0] for row in evaluations}))
        self.assertTrue(all(inside and depth == 1 for _, inside, depth in evaluations))
        self.assertFalse(connection.in_atomic_block)
        print(f'LOCK AUDIT: backend={connection.vendor}; evaluated Sale/Ingredient/IngredientStockMove '
              f'select_for_update queries; all inside caller atomic depth=1; '
              f'database supports FOR UPDATE={connection.features.has_select_for_update}')

    def test_deduction_error_rolls_back_entire_bill(self):
        with patch.object(IngredientStockMove.objects, 'create', side_effect=RuntimeError('test failure')):
            with self.assertRaisesMessage(RuntimeError, 'test failure'):
                self.create_sale()
        self.assertFalse(Sale.objects.filter(tenant=self.tenant).exists())
        self.assertFalse(SaleItem.objects.exists())
        self.rice.refresh_from_db()
        self.oil.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal('10.00'))
        self.assertEqual(self.oil.current_stock, Decimal('5.00'))