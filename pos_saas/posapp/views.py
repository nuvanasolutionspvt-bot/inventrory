from datetime import date, timedelta
from decimal import Decimal
import base64
import hashlib
import hmac
import logging
import urllib.error
import urllib.request
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, permission_required, user_passes_test
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Sum, F, DecimalField, ExpressionWrapper, Q, Value, Subquery, OuterRef, Count, Case, When, CharField

)
from django.db.models.functions import Coalesce, Cast
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string  # NEW
from django.utils import timezone

from .forms import (
    ProductForm, SiteSettingForm, SupplierForm, CustomerForm,
    PurchaseForm, SaleForm, StockAdjustForm, ProductSetForm,
    UserCreateForm, UserEditForm, RoleForm, RolePermissionForm,
    TenantRegistrationForm, CompanyBusinessCreateForm, CompanyBusinessEditForm,
    # NEW credit forms
    ReceivePaymentForm, CustomerChargeForm, CustomerStatementFilterForm,
)
from .models import (
    Product, ProductBatch, ProductSet, ProductSetItem, SiteSetting, Supplier, Customer, Purchase, PurchaseItem,
    Sale, SaleItem, StockMove, Category, CustomerLedger, TenantMembership,
    SubscriptionPlan, TenantSubscription, SubscriptionPaymentOrder, Tenant
)
from .tenancy import SESSION_TENANT_KEY, require_active_tenant
import csv, io, json
from django.contrib.auth.models import User, Group, Permission

logger = logging.getLogger(__name__)

# PDF / Barcode libs
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.graphics.barcode import code128, createBarcodeDrawing
from reportlab.graphics import renderPDF
from reportlab.lib.utils import simpleSplit


# -------------------------------------------------------------------
# Helpers: credit enforcement, ledger posting, (optional) SMS notifier
# -------------------------------------------------------------------

def _tenant(request):
    return require_active_tenant(request)


def _current_subscription(tenant):
    return (
        TenantSubscription.objects
        .filter(tenant=tenant, status='active', ends_at__gte=timezone.now())
        .select_related('plan')
        .order_by('-ends_at')
        .first()
    )


def _activate_subscription(tenant, plan, razorpay_order_id='', razorpay_payment_id=''):
    now = timezone.now()
    current = _current_subscription(tenant)
    starts_at = current.ends_at if current and current.ends_at > now and plan.is_paid else now
    subscription = TenantSubscription.objects.create(
        tenant=tenant,
        plan=plan,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=plan.duration_days),
        status='active',
        razorpay_order_id=razorpay_order_id,
        razorpay_payment_id=razorpay_payment_id,
    )
    tenant.plan = plan.code
    tenant.is_active = True
    tenant.save(update_fields=['plan', 'is_active', 'updated_at'])
    return subscription


def _razorpay_configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def _create_razorpay_order(plan, tenant):
    amount_paise = int((plan.price * Decimal('100')).quantize(Decimal('1')))
    payload = {
        'amount': amount_paise,
        'currency': 'INR',
        'receipt': f'sub_{tenant.id}_{plan.code}_{timezone.now():%Y%m%d%H%M%S}',
        'notes': {
            'tenant_id': str(tenant.id),
            'tenant': tenant.name,
            'plan': plan.code,
        },
    }
    request = urllib.request.Request(
        'https://api.razorpay.com/v1/orders',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    token = base64.b64encode(
        f'{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}'.encode('utf-8')
    ).decode('ascii')
    request.add_header('Authorization', f'Basic {token}')

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Razorpay order failed: {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Could not connect to Razorpay: {exc.reason}') from exc


def _valid_razorpay_signature(order_id, payment_id, signature):
    message = f'{order_id}|{payment_id}'.encode('utf-8')
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode('utf-8'),
        message,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or '')


def _company_admin_required(user):
    return user.is_authenticated and user.is_superuser


def _business_rows():
    subscriptions = {}
    rows = []
    seen = set()
    for sub in (
        TenantSubscription.objects
        .filter(status='active')
        .select_related('plan')
        .order_by('tenant_id', '-ends_at')
    ):
        if sub.tenant_id not in seen:
            subscriptions[sub.tenant_id] = sub
            seen.add(sub.tenant_id)
    for tenant in Tenant.objects.order_by('-created_at'):
        rows.append({
            'tenant': tenant,
            'subscription': subscriptions.get(tenant.id),
        })
    return rows


def _send_sms_if_enabled(customer: Customer, message: str):
    """Lightweight SMS hook. Replace with real gateway call if needed."""
    s = SiteSetting.get(customer.tenant)
    if not (s.sms_enabled and customer and customer.sms_opt_in and customer.phone):
        return
    # Integrate your gateway here (Textlocal / MSG91). For now, log via messages.
    # e.g., requests.post(...)  — intentionally omitted.
    # messages.info(request, f"SMS to {customer.phone}: {message}")  # if you pass request
    # Since we may not have request in helpers, simply no-op here.


def _enforce_credit_or_block(customer: Customer, will_add_debit: Decimal):
    """Customer credit is unlimited, so credit sales are always allowed."""
    return None


def _maybe_credit_alert(customer: Customer, added_debit: Decimal):
    """Limit-based alerts are disabled because customer credit is unlimited."""
    return


def _product_stock_map(tenant, product_ids=None):
    qs = StockMove.objects.filter(tenant=tenant)
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    rows = qs.values('product_id').annotate(s=Coalesce(Sum('change'), 0))
    return {r['product_id']: int(r['s'] or 0) for r in rows}


def _set_available_stock(product_set, tenant, stock_map=None):
    stock_map = stock_map or _product_stock_map(
        tenant,
        product_set.items.values_list('product_id', flat=True)
    )
    possible = []
    for set_item in product_set.items.all():
        possible.append(stock_map.get(set_item.product_id, 0) // int(set_item.qty or 1))
    return min(possible) if possible else 0


def _pos_catalog(tenant):
    products = list(
        Product.objects.filter(tenant=tenant, is_active=True)
        .annotate(stock_sum=Coalesce(Sum('stockmove__change'), 0))
        .order_by('code')
    )
    sets = list(
        ProductSet.objects.filter(tenant=tenant, is_active=True)
        .prefetch_related('items__product')
        .order_by('code')
    )
    stock_map = _product_stock_map(tenant)
    for product_set in sets:
        product_set.stock_sum = _set_available_stock(product_set, tenant, stock_map)
    batches = []
    if tenant.business_type == 'pharmacy':
        # Include depleted/expired batches so a return can target its original
        # batch. Normal-sale validation still blocks either condition.
        batches = list(
            ProductBatch.objects
            .filter(tenant=tenant, received_qty__gt=0, product__is_active=True)
            .select_related('product')
            .order_by('product__code', 'expiry_date', 'batch_no')
        )
        batches.sort(key=lambda batch: (
            batch.product.code,
            0 if batch.stock_available() else 1,
            batch.expiry_date,
            batch.batch_no,
        ))
    return products, sets, batches


def _line_from_pos_item(item, tenant):
    catalog_key = (item.get('catalog_key') or '').strip()
    if ':' in catalog_key:
        key_kind, key_id = catalog_key.split(':', 1)
        if key_kind in ('product', 'set', 'batch') and key_id:
            item = dict(item)
            item['kind'] = key_kind
            if key_kind == 'set':
                item['set_id'] = key_id
                item.pop('product_id', None)
            elif key_kind == 'batch':
                item['batch_id'] = key_id
            else:
                item['product_id'] = key_id
                item.pop('set_id', None)

    kind = item.get('kind') or item.get('type') or ('set' if item.get('set_id') else 'product')
    qty = int(item.get('qty') or 0)
    unit_price = Decimal(str(item.get('unit_price') or item.get('price') or 0))
    if qty <= 0:
        raise ValidationError('Quantity must be greater than zero.')

    if kind == 'batch':
        batch = get_object_or_404(
            ProductBatch.objects.select_related('product'),
            tenant=tenant,
            pk=item.get('batch_id'),
        )
        # Pharmacy selling price is owned by the selected batch. Never trust a
        # browser-submitted price for a batch sale.
        unit_price = batch.sale_price
        product = batch.product
        return {
            'kind': 'batch',
            'product': product,
            'product_batch': batch,
            'product_set': None,
            'qty': qty,
            'unit_price': unit_price,
            'tax_percent': product.tax_percent or 0,
            'description': f"{product.code} - {product.name}",
            'details': f"Batch: {batch.batch_no} | Exp: {batch.expiry_date.isoformat()}",
            'components': None,
            'source_label': f"batch {batch.batch_no} of {product.code} - {product.name}",
        }

    # Legacy/stale POS JavaScript can submit a set row as product_id=<same id>.
    # If the id and price clearly point to a ProductSet, bill it as the set.
    if kind == 'product' and item.get('product_id') and not catalog_key:
        possible_set = ProductSet.objects.filter(
            tenant=tenant,
            pk=item.get('product_id'),
            is_active=True,
        ).first()
        if possible_set and unit_price == Decimal(str(possible_set.unit_price or 0)):
            kind = 'set'
            item = dict(item)
            item['set_id'] = possible_set.pk
            item.pop('product_id', None)

    if kind == 'set':
        product_set = get_object_or_404(
            ProductSet.objects.filter(tenant=tenant).prefetch_related('items__product'),
            pk=item.get('set_id') or item.get('product_id')
        )
        return {
            'kind': 'set',
            'product': None,
            'product_batch': None,
            'product_set': product_set,
            'qty': qty,
            'unit_price': unit_price,
            'tax_percent': product_set.tax_percent or 0,
            'description': f"{product_set.code} - {product_set.name}",
            'details': "\n".join(
                f"{component.product.code} - {component.product.name} x {component.qty}"
                for component in product_set.items.all()
            ),
            'components': list(product_set.items.all()),
            'source_label': f"set {product_set.code} - {product_set.name}",
        }

    product = get_object_or_404(Product, tenant=tenant, pk=item.get('product_id'))
    if tenant.business_type == 'pharmacy':
        raise ValidationError(f'Select a batch for {product.code} - {product.name}.')
    return {
        'kind': 'product',
        'product': product,
        'product_batch': None,
        'product_set': None,
        'qty': qty,
        'unit_price': unit_price,
        'tax_percent': product.tax_percent or 0,
        'description': f"{product.code} - {product.name}",
        'details': '',
        'components': None,
        'source_label': f"direct item {product.code} - {product.name}",
    }


def _required_stock_for_lines(lines):
    required = {}
    labels = {}
    for line in lines:
        qty = int(line['qty'] or 0)
        if line['kind'] == 'set':
            for component in line['components']:
                need = qty * int(component.qty or 1)
                required[component.product_id] = required.get(component.product_id, 0) + need
                labels[component.product_id] = (
                    f"{component.product.code} - {component.product.name} "
                    f"(inside {line['source_label']})"
                )
        elif line['product']:
            product = line['product']
            required[product.id] = required.get(product.id, 0) + qty
            labels[product.id] = f"{product.code} - {product.name} ({line['source_label']})"
    return required, labels


def _lock_and_validate_pharmacy_batches(lines, tenant, is_return, replacing_sale=None):
    """Lock selected batches and validate inventory before posting a sale."""
    if tenant.business_type != 'pharmacy':
        return
    if any(line['kind'] != 'batch' for line in lines):
        raise ValidationError('Pharmacy POS items must use a purchased product batch.')

    required = {}
    for line in lines:
        batch_id = line['product_batch'].id
        required[batch_id] = required.get(batch_id, 0) + int(line['qty'])

    previous_effects = {}
    previous_batch_ids = set()
    if replacing_sale is not None:
        for item in SaleItem.objects.filter(
            sale=replacing_sale,
            product_batch__isnull=False,
        ):
            previous_batch_ids.add(item.product_batch_id)
            # Effective availability after undoing the old document.
            undo_change = int(item.qty or 0)
            previous_effects[item.product_batch_id] = (
                previous_effects.get(item.product_batch_id, 0) + undo_change
            )

    locked = {
        batch.id: batch
        for batch in ProductBatch.objects.select_for_update().select_related('product').filter(
            tenant=tenant,
            id__in=set(required) | previous_batch_ids,
        )
    }
    if any(batch_id not in locked for batch_id in required):
        raise ValidationError('One or more selected batches are no longer available.')

    errors = []
    for batch_id, qty in required.items():
        batch = locked[batch_id]
        effective_available = batch.available_qty + previous_effects.get(batch_id, 0)
        if is_return:
            if effective_available + qty > batch.received_qty:
                errors.append(
                    f'{batch}: return quantity exceeds quantity previously sold from this batch.'
                )
        else:
            if batch.is_expired():
                errors.append(f'{batch}: batch expired on {batch.expiry_date}.')
            elif qty > effective_available:
                errors.append(
                    f'{batch}: requested {qty}, batch stock {effective_available}.'
                )
    if errors:
        raise ValidationError(errors)

    for line in lines:
        batch = locked[line['product_batch'].id]
        line['product_batch'] = batch
        line['product'] = batch.product
        line['unit_price'] = batch.sale_price


def _reverse_sale_batch_inventory(sale):
    """Undo batch quantities posted by an existing sale before rebuilding it."""
    items = list(
        SaleItem.objects.filter(sale=sale, product_batch__isnull=False)
        .select_related('product_batch')
    )
    if not items:
        return
    batches = {
        batch.id: batch
        for batch in ProductBatch.objects.select_for_update().filter(
            tenant=sale.tenant,
            id__in={item.product_batch_id for item in items},
        )
    }
    for item in items:
        batch = batches[item.product_batch_id]
        batch.available_qty += int(item.qty or 0)
        batch.save(update_fields=['available_qty', 'status', 'updated_at'])


def _create_sale_line_and_stock(sale, line, sign):
    qty = int(line['qty'])
    unit_price = line['unit_price']
    line_total = unit_price * qty
    tax_percent = line['tax_percent'] or 0
    tax_amount = (line_total * tax_percent / Decimal('100')).quantize(Decimal('0.01'))

    SaleItem.objects.create(
        sale=sale,
        product=line['product'],
        product_batch=line.get('product_batch'),
        product_set=line['product_set'],
        description=line['description'],
        details=line.get('details', ''),
        qty=(qty * (-1 if sale.is_return else 1)),
        unit_price=unit_price,
        line_total=line_total * sign,
        tax_percent=tax_percent,
        tax_amount=tax_amount * sign
    )

    ref = f"{'CRN' if sale.is_return else 'INV'}-{sale.id}"
    reason = 'return' if sale.is_return else 'sale'
    stock_sign = 1 if sale.is_return else -1
    if line['kind'] == 'batch':
        batch = line['product_batch']
        batch.available_qty += stock_sign * qty
        batch.save(update_fields=['available_qty', 'status', 'updated_at'])
    if line['kind'] == 'set':
        for component in line['components']:
            StockMove.objects.create(
                tenant=sale.tenant,
                product=component.product,
                change=stock_sign * qty * int(component.qty or 1),
                reason=reason,
                ref=ref
            )
    elif line['product']:
        StockMove.objects.create(
            tenant=sale.tenant,
            product=line['product'],
            change=stock_sign * qty,
            reason=reason,
            ref=ref
        )


def _post_ledger_for_sale(sale: Sale):
    """
    Post a single CustomerLedger line from the signed totals of a sale.
    - sale.total is SIGNED (+ for sale, - for return)
    - due = sale.total - sale.paid_amount
      > 0  => customer owes store => DEBIT
      < 0  => store owes customer => CREDIT
    """
    if not sale.customer:
        return
    due = (sale.total or Decimal('0')) - (sale.paid_amount or Decimal('0'))
    if due == 0:
        return
    if due > 0:
        CustomerLedger.objects.create(
            tenant=sale.tenant,
            customer=sale.customer, date=sale.date,
            description=f"{'INV' if not sale.is_return else 'CRN'}-{sale.id}",
            debit=due, credit=0, sale=sale
        )
    else:
        CustomerLedger.objects.create(
            tenant=sale.tenant,
            customer=sale.customer, date=sale.date,
            description=f"{'INV' if not sale.is_return else 'CRN'}-{sale.id}",
            debit=0, credit=abs(due), sale=sale
        )


# --------------------------
# Dashboard (unchanged logic)
# --------------------------
def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = TenantRegistrationForm(request.POST)
        if form.is_valid():
            tenant, user = form.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            request.session[SESSION_TENANT_KEY] = tenant.pk
            if tenant.plan == 'trial':
                messages.success(request, f"Tenant '{tenant.name}' created. Your 7-day free trial is active.")
            else:
                messages.success(request, f"Tenant '{tenant.name}' created. Complete payment to activate the selected plan.")
            return redirect('subscription')
    else:
        form = TenantRegistrationForm()

    return render(request, 'auth/register.html', {'form': form})


@user_passes_test(_company_admin_required, login_url='company_login')
def company_dashboard(request):
    rows = _business_rows()
    active_count = sum(1 for row in rows if row['tenant'].is_active)
    expired_count = sum(
        1 for row in rows
        if row['subscription'] and row['subscription'].ends_at < timezone.now()
    )
    return render(request, 'company/dashboard.html', {
        'total_businesses': Tenant.objects.count(),
        'active_businesses': active_count,
        'expired_businesses': expired_count,
        'recent_rows': rows[:5],
    })


@user_passes_test(_company_admin_required, login_url='company_login')
def company_business_list(request):
    rows = _business_rows()
    return render(request, 'company/business_list.html', {'rows': rows})


@user_passes_test(_company_admin_required, login_url='company_login')
def company_business_create(request):
    if request.method == 'POST':
        form = CompanyBusinessCreateForm(request.POST)
        if form.is_valid():
            tenant, user = form.save()
            messages.success(request, f"Business '{tenant.name}' registered for {tenant.owner_name}.")
            return redirect('company_business_list')
    else:
        form = CompanyBusinessCreateForm()
    return render(request, 'company/business_form.html', {
        'form': form,
        'title': 'Register Business',
        'business': None,
    })


@user_passes_test(_company_admin_required, login_url='company_login')
def company_business_edit(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    subscription = (
        TenantSubscription.objects
        .filter(tenant=tenant, status='active')
        .select_related('plan')
        .order_by('-ends_at')
        .first()
    )
    if request.method == 'POST':
        form = CompanyBusinessEditForm(request.POST, instance=tenant, subscription=subscription)
        if form.is_valid():
            business = form.save()
            messages.success(request, f"Business '{business.name}' updated.")
            return redirect('company_business_list')
    else:
        form = CompanyBusinessEditForm(instance=tenant, subscription=subscription)
    return render(request, 'company/business_form.html', {
        'form': form,
        'title': 'Edit Business',
        'business': tenant,
    })


@login_required
def subscription(request):
    tenant = _tenant(request)
    plans = SubscriptionPlan.objects.filter(is_active=True).order_by('price', 'duration_days')
    current = _current_subscription(tenant)
    return render(request, 'subscription/index.html', {
        'tenant': tenant,
        'plans': plans,
        'current_subscription': current,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
        'razorpay_configured': _razorpay_configured(),
    })


@login_required
@transaction.atomic
def subscription_trial(request):
    if request.method != 'POST':
        return redirect('subscription')

    tenant = _tenant(request)
    plan = get_object_or_404(SubscriptionPlan, code='trial', is_active=True)
    if TenantSubscription.objects.filter(tenant=tenant, plan=plan).exists():
        messages.warning(request, 'Free trial was already used for this business.')
        return redirect('subscription')

    _activate_subscription(tenant, plan)
    messages.success(request, '7-day free trial activated.')
    return redirect('subscription')


@login_required
@transaction.atomic
def subscription_checkout(request, plan_code):
    if request.method != 'POST':
        return redirect('subscription')

    tenant = _tenant(request)
    plan = get_object_or_404(SubscriptionPlan, code=plan_code, is_active=True, is_paid=True)

    if not _razorpay_configured():
        messages.error(request, 'Razorpay keys are not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.')
        return redirect('subscription')

    try:
        order = _create_razorpay_order(plan, tenant)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect('subscription')

    payment_order = SubscriptionPaymentOrder.objects.create(
        tenant=tenant,
        plan=plan,
        amount=plan.price,
        currency='INR',
        razorpay_order_id=order['id'],
    )
    return render(request, 'subscription/payment.html', {
        'tenant': tenant,
        'plan': plan,
        'payment_order': payment_order,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
        'amount_paise': order['amount'],
        'callback_url': request.build_absolute_uri('/subscription/verify/'),
    })


@login_required
@transaction.atomic
def subscription_verify(request):
    if request.method != 'POST':
        return redirect('subscription')

    tenant = _tenant(request)
    order_id = request.POST.get('razorpay_order_id', '').strip()
    payment_id = request.POST.get('razorpay_payment_id', '').strip()
    signature = request.POST.get('razorpay_signature', '').strip()

    payment_order = get_object_or_404(
        SubscriptionPaymentOrder.objects.select_for_update().select_related('plan'),
        tenant=tenant,
        razorpay_order_id=order_id,
    )

    if not _valid_razorpay_signature(order_id, payment_id, signature):
        payment_order.status = 'failed'
        payment_order.razorpay_payment_id = payment_id
        payment_order.razorpay_signature = signature
        payment_order.save(update_fields=['status', 'razorpay_payment_id', 'razorpay_signature', 'updated_at'])
        messages.error(request, 'Payment verification failed. No subscription was activated.')
        return redirect('subscription')

    if payment_order.status != 'paid':
        payment_order.status = 'paid'
        payment_order.razorpay_payment_id = payment_id
        payment_order.razorpay_signature = signature
        payment_order.save(update_fields=['status', 'razorpay_payment_id', 'razorpay_signature', 'updated_at'])
        _activate_subscription(
            tenant,
            payment_order.plan,
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
        )

    messages.success(request, f'{payment_order.plan.name} activated successfully.')
    return redirect('subscription')


@login_required
def dashboard(request):
    tenant = _tenant(request)
    today = date.today()

    expiry_kpis = None
    if tenant.business_type == 'pharmacy':
        day_30 = today + timedelta(days=30)
        day_60 = today + timedelta(days=60)
        day_90 = today + timedelta(days=90)
        batch_value = ExpressionWrapper(
            F('available_qty') * F('purchase_rate'),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
        available_batches = ProductBatch.objects.filter(tenant=tenant, available_qty__gt=0)
        expiry_kpis = available_batches.aggregate(
            expired_count=Count('id', filter=Q(expiry_date__lt=today)),
            days_30_count=Count('id', filter=Q(expiry_date__gte=today, expiry_date__lte=day_30)),
            days_60_count=Count('id', filter=Q(expiry_date__gt=day_30, expiry_date__lte=day_60)),
            days_90_count=Count('id', filter=Q(expiry_date__gt=day_60, expiry_date__lte=day_90)),
            expired_value=Coalesce(
                Sum(batch_value, filter=Q(expiry_date__lt=today)),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
            near_expiry_value=Coalesce(
                Sum(batch_value, filter=Q(expiry_date__gte=today, expiry_date__lte=day_90)),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
        )
        expiry_kpis['alert_count'] = (
            expiry_kpis['expired_count'] + expiry_kpis['days_30_count'] +
            expiry_kpis['days_60_count'] + expiry_kpis['days_90_count']
        )

    # ===== Basic KPIs =====
    total_sales_today = (
        Sale.objects.filter(tenant=tenant, date=today)
        .aggregate(s=Coalesce(Sum('total'), Value(Decimal('0.00'))))['s']
    )
    total_products = Product.objects.filter(tenant=tenant).count()

    # Low stock count & details
    low_stock_qs = (
        Product.objects.filter(tenant=tenant).select_related('category')
        .annotate(stock_sum=Coalesce(Sum('stockmove__change'), Value(0)))
    )
    low_stock = low_stock_qs.filter(stock_sum__lte=F('reorder_level')).count()
    low_stock_details = (
        low_stock_qs.filter(stock_sum__lte=F('reorder_level'))
        .order_by('stock_sum', 'code')[:15]
    )

    # ===== Top selling & customers (last 30 days) =====
    start_30 = today - timedelta(days=30)

    top_products = (
        SaleItem.objects
        .filter(sale__tenant=tenant, sale__is_return=False, sale__date__gte=start_30)
        .values('product__id', 'product__code', 'product__name')
        .annotate(
            total_qty=Coalesce(Sum('qty'), Value(0)),
            revenue=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('qty') * F('unit_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2)
                    )
                ),
                Value(Decimal('0.00'))
            ),
        )
        .order_by('-total_qty')[:10]
    )

    # Subqueries to find each customer's most purchased product (qty) in last 30 days
    top_prod_base = (
        SaleItem.objects
        .filter(sale__tenant=tenant, sale__is_return=False, sale__date__gte=start_30, sale__customer=OuterRef('customer_id'))
        .values('product__id')
        .annotate(qty_sum=Coalesce(Sum('qty'), Value(0)))
        .order_by('-qty_sum', 'product__id')
    )
    top_prod_name_sq = top_prod_base.values('product__name')[:1]
    top_prod_code_sq = top_prod_base.values('product__code')[:1]
    top_prod_qty_sq  = top_prod_base.values('qty_sum')[:1]

    top_customers = (
        Sale.objects.filter(tenant=tenant, is_return=False, date__gte=start_30)
        .values('customer_id', 'customer__name')
        .annotate(
            invoices=Count('id'),
            total=Coalesce(Sum('total'), Value(Decimal('0.00'))),
            top_product=Subquery(top_prod_name_sq),
            top_product_code=Subquery(top_prod_code_sq),
            top_product_qty=Subquery(top_prod_qty_sq),
        )
        .order_by('-total')[:10]
    )

    # ===== Credit overview =====
    # Note: DO NOT annotate a field named "balance" (conflicts with @property). Use "bal".
    customers_balanced = (
        Customer.objects.filter(tenant=tenant)
        .annotate(
            debit_sum=Coalesce(Sum('customerledger__debit'),  Value(Decimal('0.00'))),
            credit_sum=Coalesce(Sum('customerledger__credit'), Value(Decimal('0.00')))
        )
        .annotate(
            bal=ExpressionWrapper(
                F('debit_sum') - F('credit_sum'),
                output_field=DecimalField(max_digits=14, decimal_places=2)
            )
        )
    )

    # Total outstanding = sum of positive balances
    total_outstanding = (
        customers_balanced.filter(bal__gt=0)
        .aggregate(t=Coalesce(Sum('bal'), Value(Decimal('0.00'))))['t']
    )

    # Top debtors (balances > 0)
    top_debtors = (
        customers_balanced
        .filter(bal__gt=0)
        .values('id', 'name', 'bal')
        .order_by('-bal')[:10]
    )

    credit_kpis = {
        'total_outstanding': total_outstanding or Decimal('0.00'),
        'customers_with_balance': customers_balanced.filter(bal__gt=0).count(),
    }

    return render(request, 'dashboard.html', {
        # Core KPIs
        'total_sales_today': total_sales_today or Decimal('0.00'),
        'total_products': total_products,
        'low_stock': low_stock,
        'low_stock_details': low_stock_details,
        'top_products': top_products,
        'top_customers': top_customers,
        # Credit
        'credit_kpis': credit_kpis,
        'top_debtors': top_debtors,
        'expiry_kpis': expiry_kpis,
    })

# --------------------------
# Pager Helper (unchanged)
# --------------------------

def _pager_ctx(request, queryset, default_size=25):
    try:
        page_size = int(request.GET.get('page_size') or default_size)
    except (TypeError, ValueError):
        page_size = default_size
    if page_size not in (10, 25, 50, 100, 200):
        page_size = default_size

    paginator = Paginator(queryset, page_size)
    page_number = request.GET.get('page') or 1
    page_obj = paginator.get_page(page_number)

    qs_copy = request.GET.copy()
    qs_copy.pop('page', None)
    base_qs = qs_copy.urlencode()
    return page_obj, page_size, base_qs


# -------------
# Masters (same)
# -------------

@login_required
def product_list(request):
    tenant = _tenant(request)
    q = (request.GET.get('q') or '').strip()
    products = Product.objects.filter(tenant=tenant).select_related('category')
    if tenant.business_type == 'pharmacy':
        products = products.annotate(stock_sum=Coalesce(Sum('batches__available_qty'), 0))
    else:
        products = products.annotate(stock_sum=Coalesce(Sum('stockmove__change'), 0))
    products = products.order_by('code')
    if q:
        products = products.filter(
            Q(name__icontains=q) | Q(code__icontains=q) | Q(barcode__icontains=q)
        )

    page_obj, page_size, base_qs = _pager_ctx(request, products)
    return render(request, 'products/list.html', {
        'q': q,
        'page_obj': page_obj,
        'page_size': page_size,
        'base_qs': base_qs,
        'is_pharmacy': tenant.business_type == 'pharmacy',
    })


@login_required
def product_create(request):
    tenant = _tenant(request)
    if request.method == 'POST':
        form = ProductForm(request.POST, tenant=tenant)
        if form.is_valid():
            form.save()
            messages.success(request, 'Product created.')
            return redirect('product_list')
    else:
        form = ProductForm(tenant=tenant)
    return render(request, 'products/form.html', {'form': form, 'title': 'New Product'})


@login_required
def product_update(request, pk):
    tenant = _tenant(request)
    product = get_object_or_404(Product, tenant=tenant, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product, tenant=tenant)
        if form.is_valid():
            form.save()
            messages.success(request, 'Product updated.')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product, tenant=tenant)
    return render(request, 'products/form.html', {'form': form, 'title': 'Edit Product'})


@login_required
@permission_required('posapp.view_productset', raise_exception=True)
def product_set_list(request):
    tenant = _tenant(request)
    q = (request.GET.get('q') or '').strip()
    sets = ProductSet.objects.filter(tenant=tenant).prefetch_related('items__product').order_by('code')
    if q:
        sets = sets.filter(Q(name__icontains=q) | Q(code__icontains=q))
    page_obj, page_size, base_qs = _pager_ctx(request, sets)
    return render(request, 'sets/list.html', {
        'page_obj': page_obj,
        'page_size': page_size,
        'base_qs': base_qs,
        'q': q,
    })


@login_required
@permission_required('posapp.add_productset', raise_exception=True)
@transaction.atomic
def product_set_create(request):
    return _product_set_save(request)


@login_required
@permission_required('posapp.change_productset', raise_exception=True)
@transaction.atomic
def product_set_update(request, pk):
    tenant = _tenant(request)
    product_set = get_object_or_404(ProductSet, tenant=tenant, pk=pk)
    return _product_set_save(request, product_set)


def _product_set_save(request, product_set=None):
    tenant = _tenant(request)
    products = Product.objects.filter(tenant=tenant, is_active=True).order_by('code')
    if request.method == 'POST':
        form = ProductSetForm(request.POST, instance=product_set, tenant=tenant)
        items_json = request.POST.get('items_json', '[]')
        try:
            items = json.loads(items_json)
        except Exception:
            items = []

        clean_items = []
        seen = set()
        for item in items:
            try:
                product_id = int(item.get('product_id'))
                qty = int(item.get('qty') or 0)
            except Exception:
                continue
            if (
                product_id and qty > 0 and product_id not in seen and
                products.filter(pk=product_id).exists()
            ):
                clean_items.append({'product_id': product_id, 'qty': qty})
                seen.add(product_id)

        if form.is_valid() and clean_items:
            obj = form.save()
            ProductSetItem.objects.filter(product_set=obj).delete()
            for item in clean_items:
                ProductSetItem.objects.create(
                    product_set=obj,
                    product_id=item['product_id'],
                    qty=item['qty']
                )
            messages.success(request, f'Set "{obj.name}" saved.')
            return redirect('product_set_list')

        if not clean_items:
            messages.error(request, 'Add at least one product to the set.')
    else:
        form = ProductSetForm(instance=product_set, tenant=tenant)

    existing_items = []
    if product_set:
        for item in product_set.items.select_related('product').all():
            existing_items.append({
                'product_id': item.product_id,
                'qty': item.qty,
            })

    return render(request, 'sets/form.html', {
        'form': form,
        'products': products,
        'product_set': product_set,
        'items_json': json.dumps(existing_items),
        'title': 'Edit Set' if product_set else 'New Set',
    })


@login_required
def product_export(request):
    tenant = _tenant(request)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="products.csv"'
    writer = csv.writer(response)
    headers = ['code', 'barcode', 'name']
    headers.extend(['category', 'unit_price', 'cost_price', 'tax_percent', 'reorder_level', 'is_active'])
    writer.writerow(headers)
    for p in Product.objects.filter(tenant=tenant).select_related('category').order_by('code'):
        row = [p.code or '', p.barcode or '', p.name]
        row.extend([
            (p.category.name if p.category else ''),
            p.unit_price, p.cost_price, p.tax_percent, p.reorder_level, int(p.is_active)
        ])
        writer.writerow(row)
    return response


@login_required
def product_import(request):
    tenant = _tenant(request)
    if request.method != 'POST' or 'file' not in request.FILES:
        messages.error(request, 'Upload a CSV file.')
        return redirect('product_list')
    f = io.TextIOWrapper(request.FILES['file'].file, encoding='utf-8')
    reader = csv.DictReader(f)
    count = 0
    for row in reader:
        code = (row.get('code') or '').strip()
        if not code:
            continue
        barcode = (row.get('barcode') or '').strip() or None
        name = (row.get('name') or '').strip()
        cat_name = (row.get('category') or '').strip() or None
        unit_price = Decimal(row.get('unit_price') or '0')
        cost_price = Decimal(row.get('cost_price') or '0')
        tax_percent = Decimal(row.get('tax_percent') or '0')
        reorder_level = int(row.get('reorder_level') or 0)
        is_active = (row.get('is_active') or '1') in ('1','true','True','yes','YES')
        category = None
        if cat_name:
            category, _ = Category.objects.get_or_create(tenant=tenant, name=cat_name)
        Product.objects.update_or_create(
            tenant=tenant,
            code=code,
            defaults={
                'barcode': barcode, 'name': name,
                'category': category,
                'unit_price': unit_price, 'cost_price': cost_price,
                'tax_percent': tax_percent, 'reorder_level': reorder_level,
                'is_active': is_active
            }
        )
        count += 1
    messages.success(request, f'Imported {count} products.')
    return redirect('product_list')


@login_required
def supplier_list(request):
    tenant = _tenant(request)
    suppliers = Supplier.objects.filter(tenant=tenant).order_by('name')
    page_obj, page_size, base_qs = _pager_ctx(request, suppliers)
    return render(request, 'suppliers/list.html', {
        'page_obj': page_obj,
        'page_size': page_size,
        'base_qs': base_qs,
    })


@login_required
def supplier_create(request):
    tenant = _tenant(request)
    if request.method == 'POST':
        form = SupplierForm(request.POST, tenant=tenant)
        if form.is_valid():
            form.save()
            messages.success(request, 'Supplier created.')
            return redirect('supplier_list')
    else:
        form = SupplierForm(tenant=tenant)
    return render(request, 'suppliers/form.html', {'form': form, 'title': 'New Supplier'})


@login_required
def customer_list(request):
    tenant = _tenant(request)
    customers = Customer.objects.filter(tenant=tenant).order_by('name')
    page_obj, page_size, base_qs = _pager_ctx(request, customers)
    return render(request, 'customers/list.html', {
        'page_obj': page_obj,
        'page_size': page_size,
        'base_qs': base_qs,
    })


@login_required
def customer_create(request):
    tenant = _tenant(request)
    if request.method == 'POST':
        form = CustomerForm(request.POST, tenant=tenant)
        if form.is_valid():
            form.save()
            messages.success(request, 'Customer created.')
            return redirect('customer_list')
    else:
        form = CustomerForm(tenant=tenant)
    return render(request, 'customers/form.html', {'form': form, 'title': 'New Customer'})

# ---------- Helpers ----------
def _subset_form(form: CustomerForm, preferred_fields=None):
    """
    Restrict the form to a smaller subset for the quick modal,
    while preserving original widgets/validators.
    """
    preferred_fields = preferred_fields or ['name', 'phone', 'email', 'address']
    keep = [f for f in preferred_fields if f in form.fields]
    form.fields = {k: form.fields[k] for k in keep}
    return form


# ---------- Quick Add endpoints ----------
@login_required
def customer_quick_new(request):
    """
    Renders a subset of CustomerForm as HTML (to be injected into the modal body).
    """
    form = CustomerForm(tenant=_tenant(request))
    form = _subset_form(form)
    return render(request, 'customers/_quick_form.html', {'form': form})


@login_required
def customer_quick_create(request):
    """
    Validates & creates via CustomerForm. Accepts JSON or form-encoded.
    Returns JSON:
      {ok:true, id:<pk>, display:"CODE - Name"}  OR
      {ok:false, errors:{field:[...], '__all__':[...]} }
    """
    if request.content_type == 'application/json':
        payload = json.loads(request.body or '{}')
        form = CustomerForm(payload, tenant=_tenant(request))
    else:
        form = CustomerForm(request.POST, tenant=_tenant(request))

    # Optional: keep the same subset for errors rendering consistency
    form = _subset_form(form)

    if form.is_valid():
        obj: Customer = form.save()
        code = getattr(obj, 'code', None)
        name = getattr(obj, 'name', str(obj))
        display = f"{code} - {name}" if code else name
        return JsonResponse({'ok': True, 'id': obj.pk, 'display': display})

    return JsonResponse({'ok': False, 'errors': form.errors}, status=400)




# ----------------
# Stock Adjustments
# ----------------

@login_required
@permission_required('posapp.can_adjust_stock', raise_exception=True)
def product_add_stock(request, pk=None):
    tenant = _tenant(request)
    if tenant.business_type == 'pharmacy':
        messages.info(request, 'Pharmacy stock must be received through a purchase batch.')
        return redirect('purchase_create')
    product = None
    if pk:
        product = get_object_or_404(Product, tenant=tenant, pk=pk)
    initial = {}
    if product:
        initial['product'] = product.id
    if request.method == 'POST':
        form = StockAdjustForm(request.POST, tenant=tenant)
        if form.is_valid():
            product = form.cleaned_data['product']
            qty = form.cleaned_data['qty']
            note = form.cleaned_data.get('note') or ''
            StockMove.objects.create(tenant=tenant, product=product, change=qty, reason='adjustment', ref=note[:64])
            messages.success(request, f"Added {qty} to stock for {product.code} — new stock: {product.stock}")
            return redirect('product_list')
    else:
        form = StockAdjustForm(initial=initial, tenant=tenant)
    return render(request, 'products/add_stock.html', {'form': form, 'product': product})


# -------- Purchases --------

def _validation_messages(exc):
    if hasattr(exc, 'message_dict'):
        messages_out = []
        for field_errors in exc.message_dict.values():
            messages_out.extend(str(err) for err in field_errors)
        return messages_out
    if hasattr(exc, 'messages'):
        return [str(msg) for msg in exc.messages]
    return [str(exc)]


def _purchase_decimal(value, field_label, row_no, errors):
    try:
        amount = Decimal(str(value if value not in (None, '') else '0'))
    except Exception:
        errors.append(f"Row {row_no}: {field_label} must be a valid number.")
        return Decimal('0.00')
    if amount < 0:
        errors.append(f"Row {row_no}: {field_label} cannot be negative.")
    return amount


def _purchase_date(value, field_label, row_no, errors):
    raw = (value or '').strip()
    if not raw:
        errors.append(f"Row {row_no}: {field_label} is required.")
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors.append(f"Row {row_no}: {field_label} must be a valid date.")
        return None


def _clean_purchase_items(tenant, raw_items, purchase=None):
    errors = []
    is_pharmacy = tenant.business_type == 'pharmacy'
    is_trade_purchase = tenant.business_type in ('retail_store', 'wholesale')
    clean_items = []

    product_ids = set()
    for item in raw_items:
        try:
            product_ids.add(int(item.get('product_id') or 0))
        except Exception:
            continue
    products = {
        product.id: product
        for product in Product.objects.filter(tenant=tenant, is_active=True, id__in=product_ids)
    }

    seen_batches = set()
    for idx, item in enumerate(raw_items, start=1):
        try:
            product_id = int(item.get('product_id') or 0)
        except Exception:
            product_id = 0
        product = products.get(product_id)
        if not product:
            errors.append(f"Row {idx}: Select a valid product.")
            continue

        try:
            qty = int(item.get('qty') or 0)
        except Exception:
            qty = 0
        if qty <= 0:
            errors.append(f"Row {idx}: Quantity must be greater than 0.")

        purchase_rate = _purchase_decimal(
            item.get('purchase_rate', item.get('cost_price', item.get('unit_price', 0))),
            'Purchase rate',
            idx,
            errors,
        )
        line = {
            'purchase_item_id': item.get('purchase_item_id') or None,
            'product_batch_id': item.get('product_batch_id') or None,
            'product': product,
            'qty': qty,
            'purchase_rate': purchase_rate,
            'line_total': purchase_rate * qty if qty > 0 else Decimal('0.00'),
        }

        if is_trade_purchase:
            line['sale_price'] = _purchase_decimal(
                item.get('sale_price', product.unit_price),
                'Selling price',
                idx,
                errors,
            )

        if is_pharmacy:
            batch_no = (item.get('batch_no') or '').strip()
            if not batch_no:
                errors.append(f"Row {idx}: Batch number is required.")

            manufacture_date = _purchase_date(item.get('manufacture_date'), 'Manufacture date', idx, errors)
            expiry_date = _purchase_date(item.get('expiry_date'), 'Expiry date', idx, errors)
            if manufacture_date and expiry_date and expiry_date <= manufacture_date:
                errors.append(f"Row {idx}: Expiry date must be after manufacture date.")

            mrp = _purchase_decimal(item.get('mrp', 0), 'MRP', idx, errors)
            sale_price = _purchase_decimal(item.get('sale_price', 0), 'Sale price', idx, errors)

            batch_key = (product.id, batch_no.lower())
            if batch_no and batch_key in seen_batches:
                errors.append(f"Row {idx}: Duplicate batch number '{batch_no}' for {product.code}.")
            seen_batches.add(batch_key)

            product_batch_id = None
            if line['product_batch_id']:
                try:
                    product_batch_id = int(line['product_batch_id'])
                except Exception:
                    errors.append(f"Row {idx}: Invalid batch reference.")
                if product_batch_id and not purchase:
                    errors.append(f"Row {idx}: Batch reference is not valid for a new purchase.")
                if product_batch_id and purchase and not PurchaseItem.objects.filter(
                    purchase=purchase,
                    product_batch_id=product_batch_id,
                ).exists():
                    errors.append(f"Row {idx}: Batch reference does not belong to this purchase.")
            duplicate_qs = ProductBatch.objects.filter(
                tenant=tenant,
                product=product,
                batch_no__iexact=batch_no,
            )
            if product_batch_id:
                duplicate_qs = duplicate_qs.exclude(pk=product_batch_id)
            if batch_no and duplicate_qs.exists():
                errors.append(f"Row {idx}: Batch number '{batch_no}' already exists for {product.code}.")

            line.update({
                'product_batch_id': product_batch_id,
                'batch_no': batch_no,
                'manufacture_date': manufacture_date,
                'expiry_date': expiry_date,
                'mrp': mrp,
                'sale_price': sale_price,
            })

        clean_items.append(line)

    if not clean_items:
        errors.append('Please add at least one item.')
    return clean_items, errors


def _batch_consumed_qty(batch):
    return max(int(batch.received_qty or 0) - int(batch.available_qty or 0), 0)


def _validate_removed_purchase_batches(purchase, clean_items):
    if not purchase:
        return
    kept_batch_ids = {
        item['product_batch_id']
        for item in clean_items
        if item.get('product_batch_id')
    }
    removed_batches = (
        ProductBatch.objects
        .filter(purchase_items__purchase=purchase)
        .exclude(pk__in=kept_batch_ids)
        .distinct()
    )
    blocked = []
    for batch in removed_batches:
        if _batch_consumed_qty(batch) > 0:
            blocked.append(str(batch))
    if blocked:
        # TODO: replace this with true batch-sale allocation checks when sales batches exist.
        raise ValidationError(
            'Cannot remove purchase batch lines that have already been partially consumed: '
            + ', '.join(blocked)
        )


def _sync_purchase_items_and_batches(purchase, clean_items, is_pharmacy):
    tenant = purchase.tenant
    is_trade_purchase = tenant.business_type in ('retail_store', 'wholesale')
    ref = f"PO-{purchase.id}"
    old_batch_ids = set(
        PurchaseItem.objects
        .filter(purchase=purchase, product_batch__isnull=False)
        .values_list('product_batch_id', flat=True)
    )
    kept_batch_ids = set()

    StockMove.objects.filter(tenant=tenant, reason='purchase', ref=ref).delete()
    PurchaseItem.objects.filter(purchase=purchase).delete()

    purchase_total = Decimal('0.00')
    for item in clean_items:
        product = item['product']
        qty = item['qty']
        purchase_rate = item['purchase_rate']
        line_total = item['line_total']
        product_batch = None

        if is_pharmacy:
            if item.get('product_batch_id'):
                product_batch = ProductBatch.objects.select_for_update().get(
                    tenant=tenant,
                    pk=item['product_batch_id'],
                )
                consumed_qty = _batch_consumed_qty(product_batch)
                if qty < consumed_qty:
                    # TODO: enforce with actual batch-wise sales once POS batch allocation exists.
                    raise ValidationError(
                        f"Cannot reduce {product_batch} below already consumed quantity {consumed_qty}."
                    )
                product_batch.product = product
                product_batch.supplier = purchase.supplier
                product_batch.batch_no = item['batch_no']
                product_batch.manufacture_date = item['manufacture_date']
                product_batch.expiry_date = item['expiry_date']
                product_batch.purchase_rate = purchase_rate
                product_batch.sale_price = item['sale_price']
                product_batch.mrp = item['mrp']
                product_batch.received_qty = qty
                product_batch.available_qty = qty - consumed_qty
                product_batch.save()
                logger.info('ProductBatch Updated: %s from %s', product_batch, ref)
            else:
                product_batch = ProductBatch.objects.create(
                    tenant=tenant,
                    product=product,
                    supplier=purchase.supplier,
                    batch_no=item['batch_no'],
                    manufacture_date=item['manufacture_date'],
                    expiry_date=item['expiry_date'],
                    purchase_rate=purchase_rate,
                    sale_price=item['sale_price'],
                    mrp=item['mrp'],
                    received_qty=qty,
                    available_qty=qty,
                )
                logger.info('ProductBatch Created: %s from %s', product_batch, ref)
            kept_batch_ids.add(product_batch.id)

        PurchaseItem.objects.create(
            purchase=purchase,
            product=product,
            product_batch=product_batch,
            qty=qty,
            cost_price=purchase_rate,
            line_total=line_total,
        )
        if is_trade_purchase:
            product.cost_price = purchase_rate
            product.unit_price = item['sale_price']
            product.save(update_fields=['cost_price', 'unit_price', 'updated_at'])
        StockMove.objects.create(
            tenant=tenant,
            product=product,
            change=qty,
            reason='purchase',
            ref=ref,
        )
        purchase_total += line_total

    if is_pharmacy:
        for product_batch in ProductBatch.objects.select_for_update().filter(pk__in=old_batch_ids - kept_batch_ids):
            consumed_qty = _batch_consumed_qty(product_batch)
            if consumed_qty > 0:
                # TODO: replace with batch-wise sale checks after POS batch allocation is implemented.
                raise ValidationError(f"Cannot remove {product_batch}; it has already been partially consumed.")
            product_batch.received_qty = 0
            product_batch.available_qty = 0
            product_batch.save(update_fields=['received_qty', 'available_qty', 'status', 'updated_at'])
            logger.info('ProductBatch Updated: %s removed from %s', product_batch, ref)

    purchase.total = purchase_total
    purchase.save(update_fields=['total', 'updated_at'])


def _purchase_initial_items(purchase):
    if not purchase:
        return '[]'
    items = []
    for item in (
        PurchaseItem.objects
        .filter(purchase=purchase)
        .select_related('product', 'product_batch')
        .order_by('id')
    ):
        batch = item.product_batch
        row = {
            'purchase_item_id': item.id,
            'product_batch_id': batch.id if batch else None,
            'kind': 'product',
            'catalog_key': f'product:{item.product_id}',
            'product_id': item.product_id,
            'qty': item.qty,
            'unit_price': str(item.cost_price),
            'cost_price': str(item.cost_price),
            'purchase_rate': str(item.cost_price),
            'sale_price': str(item.product.unit_price),
        }
        if batch:
            row.update({
                'batch_no': batch.batch_no,
                'manufacture_date': batch.manufacture_date.isoformat() if batch.manufacture_date else '',
                'expiry_date': batch.expiry_date.isoformat() if batch.expiry_date else '',
                'sale_price': str(batch.sale_price),
                'mrp': str(batch.mrp),
            })
        items.append(row)
    return json.dumps(items)


def _purchase_save(request, purchase=None):
    tenant = _tenant(request)
    is_pharmacy = tenant.business_type == 'pharmacy'
    is_trade_purchase = tenant.business_type in ('retail_store', 'wholesale')
    products = Product.objects.filter(tenant=tenant, is_active=True).order_by('code')
    editing = purchase is not None
    title = 'Edit Purchase' if editing else 'New Purchase'
    items_json = _purchase_initial_items(purchase)

    if request.method == 'POST':
        form = PurchaseForm(request.POST, instance=purchase, tenant=tenant)
        items_json = request.POST.get('items_json', '[]')
        try:
            raw_items = json.loads(items_json)
        except Exception:
            raw_items = []
        clean_items, item_errors = _clean_purchase_items(tenant, raw_items, purchase=purchase)
        if form.is_valid() and not item_errors:
            try:
                _validate_removed_purchase_batches(purchase, clean_items)
                with transaction.atomic():
                    purchase_obj = form.save(commit=False)
                    purchase_obj.tenant = tenant
                    purchase_obj.save()
                    _sync_purchase_items_and_batches(purchase_obj, clean_items, is_pharmacy)
                logger.info('Purchase %s: PO-%s', 'Updated' if editing else 'Created', purchase_obj.id)
                messages.success(request, f"Purchase PO-{purchase_obj.id} {'updated' if editing else 'saved'}.")
                return redirect('purchase_report' if editing else 'purchase_create')
            except ValidationError as exc:
                for msg in _validation_messages(exc):
                    messages.error(request, msg)
        else:
            for error in item_errors:
                messages.error(request, error)
            if not form.is_valid():
                messages.error(request, 'Please correct the purchase header details.')
    else:
        initial = None if editing else {'date': date.today()}
        form = PurchaseForm(instance=purchase, initial=initial, tenant=tenant)

    return render(request, 'purchases/new.html', {
        'form': form,
        'products': products,
        'is_pharmacy': is_pharmacy,
        'is_trade_purchase': is_trade_purchase,
        'items_json': items_json,
        'editing': editing,
        'purchase': purchase,
        'title': title,
    })


@login_required
@permission_required('posapp.can_manage_purchases', raise_exception=True)
def purchase_create(request):
    return _purchase_save(request)


@login_required
@permission_required('posapp.can_manage_purchases', raise_exception=True)
def purchase_update(request, purchase_id):
    tenant = _tenant(request)
    purchase = get_object_or_404(Purchase, tenant=tenant, pk=purchase_id)
    return _purchase_save(request, purchase=purchase)


# -------- POS Sale / Return (CREDIT-AWARE) --------

@login_required
@permission_required('posapp.can_pos', raise_exception=True)
@transaction.atomic
def pos_sale_create(request):
    tenant = _tenant(request)
    site_settings = SiteSetting.get(tenant)
    products, product_sets, product_batches = _pos_catalog(tenant)
    is_pharmacy = tenant.business_type == 'pharmacy'

    def is_ajax_req(req):
        return req.headers.get('x-requested-with') == 'XMLHttpRequest' or req.POST.get('_ajax') == '1'

    def ajax_error(message, *, status=400, extra=None):
        payload = {'ok': False, 'error': message}
        if extra:
            payload.update(extra)
        return JsonResponse(payload, status=status)

    if request.method == 'POST':
        form = SaleForm(request.POST, tenant=tenant)
        vals = request.POST.getlist('items_json')
        items_json = next((v for v in reversed(vals) if (v or '').strip()), '[]')
        try:
            items = json.loads(items_json)
        except Exception:
            items = []
        try:
            lines = [_line_from_pos_item(it, tenant) for it in items]
        except Exception:
            lines = []

        if form.is_valid() and lines:
            sale = form.save(commit=False)
            sale.tenant = tenant

            try:
                _lock_and_validate_pharmacy_batches(lines, tenant, sale.is_return)
            except ValidationError as exc:
                error_text = '\n'.join(_validation_messages(exc))
                if is_ajax_req(request):
                    return ajax_error(error_text)
                messages.error(request, error_text)
                return render(request, 'sales/pos.html', {
                    'form': form, 'products': products, 'product_sets': product_sets,
                    'product_batches': product_batches, 'is_pharmacy': is_pharmacy,
                    'items_json': items_json, 'site_settings': site_settings,
                })

            # --- HARD STOCK CHECK (normal sales only) ---
            if not sale.is_return and not is_pharmacy:
                req_by_pid, labels = _required_stock_for_lines(lines)

                if req_by_pid:
                    stock_rows = (
                        StockMove.objects
                        .filter(tenant=tenant, product_id__in=req_by_pid.keys())
                        .values('product_id')
                        .annotate(s=Coalesce(Sum('change'), 0))
                    )
                    stock_map = {r['product_id']: int(r['s'] or 0) for r in stock_rows}
                    labels = {p.id: f"{p.code} — {p.name}"
                              for p in Product.objects.filter(tenant=tenant, id__in=req_by_pid.keys()).only('id','code','name')}

                    insufficient = []
                    for pid, want in req_by_pid.items():
                        have = stock_map.get(pid, 0)
                        if want > have:
                            insufficient.append(f"{labels.get(pid, f'ID {pid}')} (requested {want}, in stock {have})")

                    if insufficient:
                        msg = "Not enough stock for:\n" + "\n".join(insufficient)
                        if is_ajax_req(request):  # NEW
                            return ajax_error(msg)
                        messages.error(request, msg)
                        return render(request, 'sales/pos.html', {
                            'form': form, 'products': products, 'product_sets': product_sets, 'items_json': items_json,
                            'product_batches': product_batches, 'is_pharmacy': is_pharmacy,
                            'site_settings': site_settings,
                        })

            # --- totals (pre-sign) ---
            subtotal = Decimal('0.00')
            tax_total = Decimal('0.00')
            for line in lines:
                qty = int(line['qty'])
                unit_price = line['unit_price']
                line_total = unit_price * qty
                tax_amount = (line_total * (line['tax_percent'] or 0) / Decimal('100')).quantize(Decimal('0.01'))
                subtotal += line_total
                tax_total += tax_amount

            sale.subtotal = subtotal
            sale.tax = tax_total
            sale.total = (subtotal - sale.discount) + tax_total

            # --- CREDIT LEDGER ---
            will_add_debit = Decimal('0.00')
            if not sale.is_return and sale.customer:
                due_if_sale = sale.total - (sale.paid_amount or Decimal('0'))
                if due_if_sale > 0:
                    will_add_debit = due_if_sale
                    msg = _enforce_credit_or_block(sale.customer, will_add_debit)
                    if msg:
                        if is_ajax_req(request):  # NEW
                            return ajax_error(msg)
                        messages.error(request, msg)
                        return render(request, 'sales/pos.html', {
                            'form': form, 'products': products, 'product_sets': product_sets, 'items_json': items_json,
                            'product_batches': product_batches, 'is_pharmacy': is_pharmacy,
                            'site_settings': site_settings,
                        })

            # sign & save sale
            sign = Decimal('-1') if sale.is_return else Decimal('1')
            sale.subtotal *= sign
            sale.tax *= sign
            sale.total *= sign
            sale.created_by = request.user
            sale.save()

            # items + stock
            for line in lines:
                _create_sale_line_and_stock(sale, line, sign)

            # ledger posting & alert
            _post_ledger_for_sale(sale)
            if will_add_debit > 0:
                _maybe_credit_alert(sale.customer, will_add_debit)
                messages.warning(request, f"Credit used: ₹{will_add_debit:.2f}. New balance ₹{(sale.customer.balance):.2f}.")

            # === NEW/CHANGED: AJAX branch returns rendered invoice HTML ===
            if is_ajax_req(request):
                # Render with the same context as invoice_view so org info appears
                items = SaleItem.objects.filter(sale=sale).select_related('product', 'product_set')
                s = site_settings
                html = render_to_string('sales/invoice.html', {
                    "sale": sale, "items": items,
                    "org_name": s.org_name, "org_address": s.org_address,
                    "org_phone": s.org_phone, "org_email": s.org_email,
                    "bill_title": s.bill_title, "bill_footer": s.bill_footer,
                    "bill_tax_inclusive": s.bill_tax_inclusive,
                    "printer_type": s.printer_type,
                }, request=request)
                return JsonResponse({
                    'ok': True,
                    'sale_id': sale.id,
                    'html': html,
                })

            messages.success(request, f"{'Return' if sale.is_return else 'Sale'} {'CRN' if sale.is_return else 'INV'}-{sale.id} saved.")
            return redirect('invoice_view', sale_id=sale.id)

        # invalid
        if is_ajax_req(request):  # NEW
            return ajax_error('Form invalid or no items.', extra={'form_errors': form.errors})
        messages.error(request, 'Form invalid or no items.')
        return render(request, 'sales/pos.html', {
            'form': form,
            'products': products,
            'product_sets': product_sets,
            'product_batches': product_batches,
            'is_pharmacy': is_pharmacy,
            'items_json': items_json,
            'site_settings': site_settings,
        })

    # GET
    form = SaleForm(initial={'date': date.today()}, tenant=tenant)
    return render(request, 'sales/pos.html', {
        'form': form,
        'products': products,
        'product_sets': product_sets,
        'product_batches': product_batches,
        'is_pharmacy': is_pharmacy,
        'site_settings': site_settings,
    })



@login_required
@permission_required('posapp.view_sale', raise_exception=True)
def invoice_view(request, sale_id):
    tenant = _tenant(request)
    sale = get_object_or_404(Sale.objects.select_related('customer'), tenant=tenant, pk=sale_id)
    items = SaleItem.objects.filter(sale=sale).select_related('product', 'product_set')
    s = SiteSetting.get(tenant)
    return render(request, 'sales/invoice.html', {
        "sale": sale, "items": items,
        "org_name": s.org_name, "org_address": s.org_address,
        "org_phone": s.org_phone, "org_email": s.org_email,
        "bill_title": s.bill_title, "bill_footer": s.bill_footer,
        "bill_tax_inclusive": s.bill_tax_inclusive,
        "printer_type": s.printer_type,
    })


# -------- Reports (unchanged) --------
@login_required
@permission_required('posapp.can_view_reports', raise_exception=True)
def sales_report(request):
    tenant = _tenant(request)
    start = request.GET.get('start')
    end = request.GET.get('end')
    qs = Sale.objects.filter(tenant=tenant)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    total = qs.aggregate(s=Sum('total'))['s'] or Decimal('0.00')
    by_day = qs.values('date').annotate(total=Sum('total')).order_by('date')

    if request.GET.get('format') == 'pdf':
        rows = [
            [
                str(s.date),
                f"{'CRN' if s.is_return else 'INV'}-{s.id}",
                s.customer.name if s.customer else '',
                f"{s.subtotal:.2f}",
                f"{s.tax:.2f}",
                f"{s.total:.2f}",
            ]
            for s in qs.order_by('date', 'id')
        ]
        title = 'Sales Report' + (f" ({start} to {end})" if start or end else '')
        return _report_pdf_response(
            title,
            ['Date', 'Inv/CRN', 'Customer', 'Subtotal', 'Tax', 'Total'],
            rows,
            footer_lines=[f"Total: {total:.2f}"],
            col_widths=[25*mm, 28*mm, 55*mm, 28*mm, 24*mm, 30*mm],
        )

    return render(request, 'reports/sales.html', {'sales': qs.order_by('-date','-id')[:200], 'total': total, 'by_day': by_day, 'start': start, 'end': end})


@login_required
@permission_required('posapp.can_view_reports', raise_exception=True)
def stock_report(request):
    tenant = _tenant(request)
    qs = (
        Product.objects
        .filter(tenant=tenant)
        .annotate(
            stock_sum=Coalesce(Sum('stockmove__change'), Value(0)),
        )
    )
    qs = qs.annotate(
        valuation=ExpressionWrapper(
            Cast(Coalesce(F('stock_sum'), 0), DecimalField(max_digits=14, decimal_places=2)) *
            Coalesce(F('cost_price'), Decimal('0')),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
    ).order_by('code')

    total_valuation = qs.aggregate(total=Coalesce(Sum('valuation'), Decimal('0')))['total']

    if request.GET.get('format') == 'pdf':
        rows = [
            [
                p.code,
                p.name,
                p.stock_sum or 0,
                f"{p.cost_price:.2f}",
                f"{p.valuation or Decimal('0'):.2f}",
            ]
            for p in qs
        ]
        return _report_pdf_response(
            'Stock Report',
            ['Code', 'Name', 'Stock', 'Cost', 'Valuation'],
            rows,
            footer_lines=[f"Total valuation: {total_valuation:.2f}"],
            col_widths=[28*mm, 72*mm, 25*mm, 30*mm, 35*mm],
        )

    page_obj, page_size, base_qs = _pager_ctx(request, qs)
    return render(request, 'reports/stock.html', {
        'page_obj': page_obj,
        'page_size': page_size,
        'base_qs': base_qs,
        'total_valuation': total_valuation,
    })


# -------- Purchases report (same) --------

def _legacy_report_pdf_response(title, headers, rows, footer_lines=None, col_widths=None):
    resp = HttpResponse(content_type='application/pdf')
    safe_title = title.lower().replace(' ', '_')
    resp['Content-Disposition'] = f'attachment; filename="{safe_title}.pdf"'

    c = canvas.Canvas(resp, pagesize=A4)
    page_w, page_h = A4
    left, right, top, bottom = 15*mm, 15*mm, 15*mm, 15*mm

    y = page_h - top
    c.setFont('Helvetica-Bold', 14)
    c.drawString(left, y, title)
    y -= 8*mm

    ncols = len(headers)
    if not col_widths:
        total_w = page_w - left - right
        col_widths = [total_w / ncols] * ncols

    def draw_header(y_pos):
        c.setFont('Helvetica-Bold', 9)
        x = left
        for i, h in enumerate(headers):
            c.drawString(x, y_pos, str(h))
            x += col_widths[i]
        return y_pos - 5*mm

    def draw_row(y_pos, row_vals):
        import re
        c.setFont('Helvetica', 9)
        x = left
        for i, val in enumerate(row_vals):
            text = str(val)
            if re.match(r'^-?\d+(?:\.\d+)?$', text.replace(',', '')) or text.strip().startswith('₹'):
                c.drawRightString(x + col_widths[i] - 2, y_pos, text)
            else:
                c.drawString(x, y_pos, text)
            x += col_widths[i]
        return y_pos - 5*mm

    y = draw_header(y)
    for r in rows:
        if y < bottom + 20:
            c.showPage()
            y = page_h - top
            c.setFont('Helvetica-Bold', 14)
            c.drawString(left, y, title)
            y -= 8*mm
            y = draw_header(y)
        y = draw_row(y, r)

    if footer_lines:
        y -= 6*mm
        c.setFont('Helvetica-Bold', 10)
        for line in footer_lines:
            if y < bottom + 12:
                c.showPage()
                y = page_h - top
            c.drawString(left, y, str(line))
            y -= 5*mm

    c.showPage()
    c.save()
    return resp


def _report_pdf_response(title, headers, rows, footer_lines=None, col_widths=None, page_size=A4):
    from xml.sax.saxutils import escape
    import re

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    resp = HttpResponse(content_type='application/pdf')
    safe_title = re.sub(r'[^a-z0-9_-]+', '_', title.lower()).strip('_') or 'report'
    resp['Content-Disposition'] = f'attachment; filename="{safe_title}.pdf"'

    ncols = len(headers)
    if not col_widths:
        page_w, _ = page_size
        total_w = page_w - 24*mm
        col_widths = [total_w / ncols] * ncols

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    header_style = ParagraphStyle(
        'ReportHeader',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=TA_LEFT,
    )
    cell_style = ParagraphStyle(
        'ReportCell',
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
    )
    numeric_style = ParagraphStyle(
        'ReportCellRight',
        parent=cell_style,
        alignment=TA_RIGHT,
    )
    footer_style = ParagraphStyle(
        'ReportFooter',
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        alignment=TA_RIGHT,
    )

    def clean_text(value):
        text = '' if value is None else str(value)
        return text.replace('â‚¹', 'Rs.').replace('₹', 'Rs.')

    def is_numeric(value):
        text = clean_text(value).strip().replace(',', '')
        text = re.sub(r'^(rs\.?|inr)\s*', '', text, flags=re.I)
        return bool(re.match(r'^-?\d+(?:\.\d+)?$', text))

    def para(value, style):
        text = escape(clean_text(value)).replace('\n', '<br/>')
        return Paragraph(text, style)

    table_data = [[para(header, header_style) for header in headers]]
    for row in rows:
        row_values = list(row)[:ncols]
        row_values += [''] * (ncols - len(row_values))
        table_data.append([
            para(value, numeric_style if is_numeric(value) else cell_style)
            for value in row_values
        ])

    doc = SimpleDocTemplate(
        resp,
        pagesize=page_size,
        leftMargin=12*mm,
        rightMargin=12*mm,
        topMargin=12*mm,
        bottomMargin=12*mm,
        title=title,
    )
    table = Table(table_data, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#334155')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#94a3b8')),
        ('BOX', (0, 0), (-1, -1), 0.7, colors.HexColor('#475569')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
    ]))

    story = [Paragraph(escape(clean_text(title)), title_style), table]
    if footer_lines:
        story.append(Spacer(1, 6*mm))
        for line in footer_lines:
            story.append(Paragraph(escape(clean_text(line)), footer_style))

    doc.build(story)
    return resp


@login_required
@permission_required('posapp.can_view_reports', raise_exception=True)
def batch_stock_report(request):
    tenant = _tenant(request)
    if tenant.business_type != 'pharmacy':
        raise PermissionDenied('Batch Stock Report is available only for pharmacy businesses.')

    purchase_date_sq = (
        PurchaseItem.objects
        .filter(product_batch_id=OuterRef('pk'), purchase__tenant=tenant)
        .order_by('purchase__date', 'purchase_id')
        .values('purchase__date')[:1]
    )
    qs = (
        ProductBatch.objects
        .filter(tenant=tenant)
        .select_related('product', 'supplier')
        .annotate(
            purchase_date=Subquery(purchase_date_sq),
            effective_status=Case(
                When(expiry_date__lt=date.today(), then=Value(ProductBatch.STATUS_EXPIRED)),
                When(available_qty__lte=0, then=Value(ProductBatch.STATUS_EMPTY)),
                default=Value(ProductBatch.STATUS_ACTIVE),
                output_field=CharField(),
            ),
            expiry_bucket=Case(
                When(expiry_date__lt=date.today(), then=Value('expired')),
                When(
                    expiry_date__gte=date.today(),
                    expiry_date__lte=date.today() + timedelta(days=90),
                    then=Value('near'),
                ),
                default=Value('normal'),
                output_field=CharField(),
            ),
            stock_value=ExpressionWrapper(
                Cast(F('available_qty'), DecimalField(max_digits=14, decimal_places=2)) * F('purchase_rate'),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
        )
    )

    q = (request.GET.get('q') or '').strip()
    product_id = (request.GET.get('product') or '').strip()
    supplier_id = (request.GET.get('supplier') or '').strip()
    batch_no = (request.GET.get('batch_no') or '').strip()
    expiry_date = (request.GET.get('expiry_date') or '').strip()
    status = (request.GET.get('status') or '').strip()
    start = (request.GET.get('start') or '').strip()
    end = (request.GET.get('end') or '').strip()
    expiry_window = (request.GET.get('expiry_window') or '').strip()
    in_stock = (request.GET.get('in_stock') or '').strip()

    def parsed_date(raw):
        try:
            return date.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            return None

    if q:
        qs = qs.filter(
            Q(product__code__icontains=q) |
            Q(product__name__icontains=q) |
            Q(batch_no__icontains=q) |
            Q(supplier__name__icontains=q)
        )
    if product_id.isdigit():
        qs = qs.filter(product_id=int(product_id))
    if supplier_id.isdigit():
        qs = qs.filter(supplier_id=int(supplier_id))
    if batch_no:
        qs = qs.filter(batch_no__icontains=batch_no)
    expiry_date_value = parsed_date(expiry_date)
    if expiry_date_value:
        qs = qs.filter(expiry_date=expiry_date_value)
    if status in dict(ProductBatch.STATUS_CHOICES):
        qs = qs.filter(effective_status=status)
    if in_stock == '1':
        qs = qs.filter(available_qty__gt=0)
    expiry_windows = {
        '30': (date.today(), date.today() + timedelta(days=30)),
        '60': (date.today() + timedelta(days=31), date.today() + timedelta(days=60)),
        '90': (date.today() + timedelta(days=61), date.today() + timedelta(days=90)),
        'near': (date.today(), date.today() + timedelta(days=90)),
    }
    if expiry_window in expiry_windows:
        window_start, window_end = expiry_windows[expiry_window]
        qs = qs.filter(expiry_date__gte=window_start, expiry_date__lte=window_end)
    start_value = parsed_date(start)
    end_value = parsed_date(end)
    if start_value:
        qs = qs.filter(purchase_date__gte=start_value)
    if end_value:
        qs = qs.filter(purchase_date__lte=end_value)

    sort_fields = {
        'product': 'product__name',
        'batch': 'batch_no',
        'supplier': 'supplier__name',
        'purchase_date': 'purchase_date',
        'manufacture_date': 'manufacture_date',
        'expiry_date': 'expiry_date',
        'available_qty': 'available_qty',
        'purchase_rate': 'purchase_rate',
        'sale_price': 'sale_price',
        'mrp': 'mrp',
        'stock_value': 'stock_value',
        'status': 'effective_status',
    }
    sort = request.GET.get('sort') or 'expiry_date'
    direction = request.GET.get('dir') or 'asc'
    order_field = sort_fields.get(sort, 'expiry_date')
    if direction == 'desc':
        order_field = '-' + order_field
    else:
        direction = 'asc'
    qs = qs.order_by(order_field, 'product__code', 'batch_no', 'id')

    total_stock_value = qs.aggregate(
        total=Coalesce(Sum('stock_value'), Decimal('0.00'))
    )['total']

    def report_rows(queryset):
        status_labels = dict(ProductBatch.STATUS_CHOICES)
        for batch in queryset:
            yield [
                f'{batch.product.code} - {batch.product.name}',
                batch.batch_no,
                batch.supplier.name if batch.supplier else '',
                batch.purchase_date.isoformat() if batch.purchase_date else '',
                batch.manufacture_date.isoformat(),
                batch.expiry_date.isoformat(),
                batch.available_qty,
                f'{batch.purchase_rate:.2f}',
                f'{batch.sale_price:.2f}',
                f'{batch.mrp:.2f}',
                f'{batch.stock_value:.2f}',
                status_labels.get(batch.effective_status, batch.effective_status.title()),
            ]

    export_format = request.GET.get('format')
    headers = [
        'Product', 'Batch Number', 'Supplier', 'Purchase Date', 'Manufacture Date',
        'Expiry Date', 'Available Qty', 'Purchase Rate', 'Sale Price', 'MRP',
        'Stock Value', 'Status',
    ]
    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="batch_stock_report.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(report_rows(qs.iterator(chunk_size=1000)))
        return response
    if export_format == 'pdf':
        from reportlab.lib.pagesizes import landscape
        return _report_pdf_response(
            'Batch Stock Report',
            headers,
            list(report_rows(qs)),
            footer_lines=[f'Total stock value: Rs. {total_stock_value:.2f}'],
            col_widths=[35*mm, 22*mm, 28*mm, 22*mm, 24*mm, 22*mm, 18*mm, 21*mm, 19*mm, 17*mm, 22*mm, 18*mm],
            page_size=landscape(A4),
        )

    page_obj, page_size, base_qs = _pager_ctx(request, qs)
    filter_params = request.GET.copy()
    for key in ('page', 'page_size', 'sort', 'dir', 'format'):
        filter_params.pop(key, None)
    export_params = request.GET.copy()
    for key in ('page', 'format'):
        export_params.pop(key, None)

    return render(request, 'reports/batch_stock.html', {
        'page_obj': page_obj,
        'page_size': page_size,
        'base_qs': base_qs,
        'filter_qs': filter_params.urlencode(),
        'export_qs': export_params.urlencode(),
        'products': Product.objects.filter(tenant=tenant, is_active=True).order_by('code'),
        'suppliers': Supplier.objects.filter(tenant=tenant).order_by('name'),
        'status_choices': ProductBatch.STATUS_CHOICES,
        'total_stock_value': total_stock_value,
        'q': q,
        'selected_product': product_id,
        'selected_supplier': supplier_id,
        'batch_no': batch_no,
        'expiry_date': expiry_date,
        'selected_status': status,
        'start': start,
        'end': end,
        'expiry_window': expiry_window,
        'in_stock': in_stock,
        'sort': sort,
        'direction': direction,
    })


@login_required
@permission_required('posapp.can_view_reports', raise_exception=True)
def purchase_report(request):
    tenant = _tenant(request)
    start = request.GET.get('start')
    end = request.GET.get('end')
    qs = Purchase.objects.filter(tenant=tenant).select_related('supplier')
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    total = qs.aggregate(s=Sum('total'))['s'] or Decimal('0.00')

    if request.GET.get('format') == 'pdf':
        headers = ['Date','PO','Supplier','Total','Notes']
        rows = []
        for p in qs.order_by('date','id'):
            rows.append([str(p.date), f'PO-{p.id}', p.supplier.name if p.supplier else '', f'₹ {p.total}', (p.notes or '')[:40]])
        title = 'Purchase Report' + (f" ({start} to {end})" if start or end else '')
        return _report_pdf_response(title, headers, rows, footer_lines=[f"Total: ₹ {total}"])
    return render(request, 'reports/purchases.html', {
        'purchases': qs.order_by('-date','-id')[:200],
        'total': total, 'start': start, 'end': end
    })


@login_required
@permission_required('posapp.view_sale', raise_exception=True)
def sales_list(request):
    tenant = _tenant(request)
    start = request.GET.get('start')
    end = request.GET.get('end')
    q = (request.GET.get('q') or '').strip()

    qs = Sale.objects.filter(tenant=tenant).select_related('customer')
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    if q:
        inv_id = None
        try:
            inv_id = int(q.replace('INV-', '').replace('CRN-', '').strip())
        except Exception:
            inv_id = None
        if inv_id:
            qs = qs.filter(id=inv_id) | qs.filter(customer__name__icontains=q)
        else:
            qs = qs.filter(customer__name__icontains=q)

    qs = qs.order_by('-date', '-id')
    total = qs.aggregate(s=Sum('total'))['s'] or Decimal('0.00')

    page_obj, page_size, base_qs = _pager_ctx(request, qs)
    return render(request, 'sales/list.html', {
        'page_obj': page_obj, 'page_size': page_size, 'base_qs': base_qs,
        'start': start, 'end': end, 'q': q, 'total': total,
    })


# -------- Update existing sale (rebuild ledger & stock) --------

@login_required
@permission_required('posapp.can_pos', raise_exception=True)
@transaction.atomic
def sale_update(request, sale_id):
    tenant = _tenant(request)
    sale = get_object_or_404(Sale, tenant=tenant, pk=sale_id)
    products, product_sets, product_batches = _pos_catalog(tenant)
    is_pharmacy = tenant.business_type == 'pharmacy'

    if request.method == 'POST':
        form = SaleForm(request.POST, instance=sale, tenant=tenant)
        items_json = request.POST.get('items_json', '[]')
        try:
            items = json.loads(items_json)
        except Exception:
            items = []
        try:
            lines = [_line_from_pos_item(it, tenant) for it in items]
        except Exception:
            lines = []

        if form.is_valid() and lines:
            try:
                _lock_and_validate_pharmacy_batches(
                    lines,
                    tenant,
                    form.cleaned_data.get('is_return', False),
                    replacing_sale=sale,
                )
            except ValidationError as exc:
                for error in _validation_messages(exc):
                    messages.error(request, error)
                lines = []

        if form.is_valid() and lines:
            sale = form.save(commit=False)
            sale.tenant = tenant

            subtotal = Decimal('0.00')
            tax_total = Decimal('0.00')
            for line in lines:
                qty = int(line['qty'])
                unit_price = line['unit_price']
                line_total = unit_price * qty
                tax_amount = (line_total * (line['tax_percent'] or 0) / Decimal('100')).quantize(Decimal('0.01'))
                subtotal += line_total
                tax_total += tax_amount

            sale.subtotal = subtotal
            sale.tax = tax_total
            sale.total = (subtotal - sale.discount) + tax_total

            # Credit ledger adjustment on edit if changing totals upward
            will_add_debit = Decimal('0.00')
            if not sale.is_return and sale.customer:
                due_if_sale = sale.total - (sale.paid_amount or Decimal('0'))
                if due_if_sale > 0:
                    will_add_debit = due_if_sale
                    msg = _enforce_credit_or_block(sale.customer, will_add_debit)
                    if msg:
                        messages.error(request, msg)
                        return render(request, 'sales/pos.html', {
                            'form': form, 'products': products, 'product_sets': product_sets, 'editing': True,
                            'product_batches': product_batches, 'is_pharmacy': is_pharmacy,
                            'sale': sale, 'prefill_items': json.dumps([]),
                            'site_settings': SiteSetting.get(tenant),
                        })

            # Validation has passed; now safely undo and rebuild postings.
            if is_pharmacy:
                _reverse_sale_batch_inventory(sale)
                for line in lines:
                    if line.get('product_batch'):
                        line['product_batch'].refresh_from_db()
            StockMove.objects.filter(tenant=tenant, ref__in=[f"INV-{sale.id}", f"CRN-{sale.id}"]).delete()
            SaleItem.objects.filter(sale=sale).delete()
            CustomerLedger.objects.filter(tenant=tenant, sale=sale).delete()

            sign = Decimal('-1') if sale.is_return else Decimal('1')
            sale.subtotal *= sign
            sale.tax *= sign
            sale.total *= sign
            sale.save()

            for line in lines:
                _create_sale_line_and_stock(sale, line, sign)

            _post_ledger_for_sale(sale)
            if will_add_debit > 0:
                _maybe_credit_alert(sale.customer, will_add_debit)
                messages.warning(request, f"Credit used: ₹{will_add_debit:.2f}. New balance ₹{(sale.customer.balance):.2f}.")

            messages.success(request, f"{'Return' if sale.is_return else 'Sale'} {'CRN' if sale.is_return else 'INV'}-{sale.id} updated.")
            return redirect('invoice_view', sale_id=sale.id)
        else:
            messages.error(request, 'Form invalid or no items.')
    else:
        form = SaleForm(instance=sale, tenant=tenant)

    prefill = []
    for it in SaleItem.objects.filter(sale=sale).select_related('product', 'product_batch', 'product_set'):
        row = {
            "kind": "batch" if it.product_batch_id else ("set" if it.product_set_id else "product"),
            "qty": abs(int(it.qty or 0)),
            "unit_price": float(it.unit_price or 0),
        }
        if it.product_batch_id:
            row["batch_id"] = it.product_batch_id
            row["product_id"] = it.product_id
        elif it.product_set_id:
            row["set_id"] = it.product_set_id
        elif it.product_id:
            row["product_id"] = it.product_id
        prefill.append(row)

    return render(request, 'sales/pos.html', {
        'form': form,
        'products': products,
        'product_sets': product_sets,
        'product_batches': product_batches,
        'is_pharmacy': is_pharmacy,
        'editing': True,
        'sale': sale,
        'prefill_items': json.dumps(prefill),
        'site_settings': SiteSetting.get(tenant),
    })


# --------------------------
# Security (users & roles)
# --------------------------

@permission_required('posapp.can_manage_users', raise_exception=True)
def security_users(request):
    tenant = _tenant(request)
    q = request.GET.get('q', '').strip()
    users = User.objects.filter(tenant_memberships__tenant=tenant).distinct().order_by('username')
    if q:
        users = users.filter(username__icontains=q) | users.filter(email__icontains=q)
    page = Paginator(users, int(request.GET.get('ps', 25))).get_page(request.GET.get('page'))
    return render(request, 'security/users_list.html', {'page': page, 'q': q})


@permission_required('posapp.can_manage_users', raise_exception=True)
def security_user_new(request):
    tenant = _tenant(request)
    if request.method == 'POST':
        form = UserCreateForm(request.POST)
        if form.is_valid():
            u = form.save()
            TenantMembership.objects.get_or_create(tenant=tenant, user=u, defaults={'role': 'staff'})
            messages.success(request, f"User '{u.username}' created.")
            return redirect('security_users')
    else:
        form = UserCreateForm()
    return render(request, 'security/user_form.html', {'form': form, 'title': 'New User'})


@permission_required('posapp.can_manage_users', raise_exception=True)
def security_user_edit(request, user_id):
    tenant = _tenant(request)
    user = get_object_or_404(User, tenant_memberships__tenant=tenant, pk=user_id)
    if request.method == 'POST':
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"User '{user.username}' updated.")
            return redirect('security_users')
    else:
        form = UserEditForm(instance=user, initial={'groups': user.groups.all()})
    return render(request, 'security/user_form.html', {'form': form, 'title': f'Edit User — {user.username}'})


@permission_required('posapp.can_manage_users', raise_exception=True)
def security_roles(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only platform administrators can manage global roles.")
    roles = Group.objects.annotate(users_count=Count('user')).order_by('name')
    return render(request, 'security/roles_list.html', {'roles': roles})


@permission_required('posapp.can_manage_users', raise_exception=True)
def security_role_new(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only platform administrators can manage global roles.")
    if request.method == 'POST':
        form = RoleForm(request.POST)
        perm_form = RolePermissionForm(request.POST)
        if form.is_valid() and perm_form.is_valid():
            g = form.save()
            g.permissions.set(perm_form.cleaned_data['permissions'])
            messages.success(request, f"Role '{g.name}' created.")
            return redirect('security_roles')
    else:
        form = RoleForm()
        perm_form = RolePermissionForm()
    return render(request, 'security/role_form.html', {'form': form, 'perm_form': perm_form, 'title': 'New Role'})


@permission_required('posapp.can_manage_users', raise_exception=True)
def security_role_edit(request, role_id):
    if not request.user.is_superuser:
        raise PermissionDenied("Only platform administrators can manage global roles.")
    g = get_object_or_404(Group, pk=role_id)
    if request.method == 'POST':
        form = RoleForm(request.POST, instance=g)
        perm_form = RolePermissionForm(request.POST)
        if form.is_valid() and perm_form.is_valid():
            form.save()
            g.permissions.set(perm_form.cleaned_data['permissions'])
            messages.success(request, f"Role '{g.name}' updated.")
            return redirect('security_roles')
    else:
        form = RoleForm(instance=g)
        perm_form = RolePermissionForm(initial={'permissions': g.permissions.filter(content_type__app_label='posapp')})
    return render(request, 'security/role_form.html', {'form': form, 'perm_form': perm_form, 'title': f'Edit Role — {g.name}'})


# --- LIVE balance for POS UI ---
@login_required
def customer_balance_api(request, customer_id):
    c = get_object_or_404(Customer, tenant=_tenant(request), pk=customer_id)
    return JsonResponse({
        'balance': str(c.balance or 0),
        'unlimited_credit': True,
        'phone': c.phone or '',
        'sms_opt_in': bool(c.sms_opt_in),
        'call_opt_in': bool(c.call_opt_in),
    })


# --- Settings (RBAC-protected) ---
@permission_required('posapp.can_manage_settings', raise_exception=True)
def settings_general(request):
    tenant = _tenant(request)
    s = SiteSetting.get(tenant)
    if request.method == 'POST':
        form = SiteSettingForm(request.POST, instance=s)
        if form.is_valid():
            form.save()
            messages.success(request, 'Settings saved.')
            return redirect('settings_general')
    else:
        form = SiteSettingForm(instance=s)
    return render(request, 'settings/general.html', {'form': form, 'title': 'POS Settings'})


# -------------------------
# CREDIT: Payments & Charges
# -------------------------

@login_required
@permission_required('posapp.can_credit_receive', raise_exception=True)
def receive_payment(request):
    """Record a payment received from a customer (ledger CREDIT)."""
    tenant = _tenant(request)
    if request.method == 'POST':
        form = ReceivePaymentForm(request.POST, tenant=tenant)
        if form.is_valid():
            c = form.cleaned_data['customer']
            amt = form.cleaned_data['amount']
            dt  = form.cleaned_data['date']
            ref = form.cleaned_data.get('reference') or ''
            payment = CustomerLedger.objects.create(
                tenant=tenant,
                customer=c, date=dt, description=f"Payment {ref}".strip(), debit=0, credit=amt
            )
            messages.success(request, f"Payment ₹{amt:.2f} recorded for {c.name}. New balance ₹{c.balance:.2f}.")
            return redirect('payment_receipt', ledger_id=payment.id)
    else:
        form = ReceivePaymentForm(initial={'date': date.today()}, tenant=tenant)
    return render(request, 'credit/receive_payment.html', {'form': form, 'title': 'Receive Payment'})


@login_required
@permission_required('posapp.can_credit_receive', raise_exception=True)
def payment_receipt(request, ledger_id):
    tenant = _tenant(request)
    payment = get_object_or_404(
        CustomerLedger.objects.select_related('customer'),
        tenant=tenant,
        pk=ledger_id,
        credit__gt=0,
        sale__isnull=True,
    )
    s = SiteSetting.get(tenant)
    return render(request, 'credit/payment_receipt.html', {
        'payment': payment,
        'org_name': s.org_name,
        'org_address': s.org_address,
        'org_phone': s.org_phone,
        'org_email': s.org_email,
        'bill_footer': s.bill_footer,
        'printer_type': s.printer_type,
        'balance': payment.customer.balance,
    })



@login_required
@permission_required('posapp.can_credit_charge', raise_exception=True)
def customer_charge(request):
    """Manual charge (opening balance / adjustment) (ledger DEBIT)."""
    tenant = _tenant(request)
    if request.method == 'POST':
        form = CustomerChargeForm(request.POST, tenant=tenant)
        if form.is_valid():
            c = form.cleaned_data['customer']
            amt = form.cleaned_data['amount']
            dt  = form.cleaned_data['date']
            reason = form.cleaned_data['reason']
            # Optional: enforce credit here too
            msg = _enforce_credit_or_block(c, amt)
            if msg:
                messages.error(request, msg)
            else:
                CustomerLedger.objects.create(
                    tenant=tenant,
                    customer=c, date=dt, description=reason[:120], debit=amt, credit=0
                )
                _maybe_credit_alert(c, amt)
                messages.success(request, f"Charge ₹{amt:.2f} posted to {c.name}. New balance ₹{c.balance:.2f}.")
                return redirect('customer_charge')
    else:
        form = CustomerChargeForm(initial={'date': date.today()}, tenant=tenant)
    return render(request, 'credit/customer_charge.html', {'form': form, 'title': 'Customer Charge / Opening Balance'})


@login_required
@permission_required('posapp.can_credit_view', raise_exception=True)
def customer_statement(request):
    """Simple customer statement with date filters."""
    tenant = _tenant(request)
    form = CustomerStatementFilterForm(request.GET or None, tenant=tenant)
    qs = CustomerLedger.objects.filter(tenant=tenant).select_related('customer').order_by('-date', '-id')

    customer = None
    if form.is_valid():
        customer = form.cleaned_data.get('customer')
        start = form.cleaned_data.get('start')
        end   = form.cleaned_data.get('end')
        if customer:
            qs = qs.filter(customer=customer)
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)

    total_debit = qs.aggregate(s=Coalesce(Sum('debit'), Decimal('0')))['s']
    total_credit = qs.aggregate(s=Coalesce(Sum('credit'), Decimal('0')))['s']
    closing = (total_debit - total_credit) if (customer or True) else Decimal('0')

    return render(request, 'credit/statement.html', {
        'form': form, 'lines': qs,
        'total_debit': total_debit, 'total_credit': total_credit,
        'closing': closing, 'customer': customer,
    })


# --------------------------
# Barcodes (unchanged layout)
# --------------------------

def _ean13_normalize(value: str):
    digits = ''.join(ch for ch in (value or '') if ch.isdigit())
    if len(digits) == 12:
        odd = sum(int(digits[i]) for i in range(0, 12, 2))
        even = sum(int(digits[i]) for i in range(1, 12, 2))
        check = (10 - ((odd + 3 * even) % 10)) % 10
        return digits + str(check)
    if len(digits) == 13:
        base = digits[:12]
        odd = sum(int(base[i]) for i in range(0, 12, 2))
        even = sum(int(base[i]) for i in range(1, 12, 2))
        check = (10 - ((odd + 3 * even) % 10)) % 10
        return base + str(check)
    return None


@login_required
@permission_required('posapp.can_print_barcodes', raise_exception=True)
def barcode_labels(request):
    tenant = _tenant(request)
    if request.method == 'GET':
        products = Product.objects.filter(tenant=tenant).order_by('code')
        product_sets = ProductSet.objects.filter(tenant=tenant).order_by('code')
        return render(request, 'products/barcodes.html', {'products': products, 'product_sets': product_sets})

    keys = request.POST.getlist('item_key')
    if not keys:
        keys = [f"product:{pid}" for pid in request.POST.getlist('product_id')]
    qtys = request.POST.getlist('qty')
    tpl = request.POST.get('tpl', 'a4_3x8')
    sym = request.POST.get('sym', 'code128')

    presets = {
        'a4_3x8': dict(cols=3, rows=8, margins=(10, 10, 10, 13)),
        'a4_3x7': dict(cols=3, rows=7, margins=(10, 10, 10, 13)),
        'a4_4x12': dict(cols=4, rows=12, margins=(8, 8, 8, 12)),
        'a4_5x13': dict(cols=5, rows=13, margins=(6, 6, 6, 10)),
        'a4_5x10': dict(cols=5, rows=10, margins=(1, 1, 1, 1)),
    }
    if tpl == 'custom':
        try:
            cols = int(request.POST.get('cols') or 3)
            rows = int(request.POST.get('rows') or 8)
            ml = float(request.POST.get('ml') or 10)
            mr = float(request.POST.get('mr') or 10)
            mt = float(request.POST.get('mt') or 10)
            mb = float(request.POST.get('mb') or 13)
            preset = dict(cols=cols, rows=rows, margins=(ml, mr, mt, mb))
        except Exception:
            preset = presets['a4_3x8']
    else:
        preset = presets.get(tpl, presets['a4_3x8'])

    cols, rows = int(preset['cols']), int(preset['rows'])
    ml, mr, mt, mb = (float(x) for x in preset['margins'])

    items = []
    for key, q in zip(keys, qtys):
        try:
            kind, raw_id = (key.split(':', 1) + [''])[:2]
            if kind == 'set':
                p = ProductSet.objects.get(tenant=tenant, pk=int(raw_id))
            else:
                p = Product.objects.get(tenant=tenant, pk=int(raw_id))
            qn = max(0, int(q))
        except Exception:
            continue
        if qn <= 0:
            continue
        raw = getattr(p, 'barcode', None) or p.code
        code_to_use = _ean13_normalize(raw) if sym == 'ean13' else raw
        if sym == 'ean13' and not code_to_use:
            code_to_use = raw
        items.extend([(p, code_to_use)] * qn)

    if not items:
        messages.error(request, 'Select at least one product or set with quantity.')
        return redirect('product_barcodes')

    page_w, page_h = A4
    left_margin, right_margin = ml * mm, mr * mm
    top_margin, bottom_margin = mt * mm, mb * mm

    label_w = (page_w - left_margin - right_margin) / cols
    label_h = (page_h - top_margin - bottom_margin) / rows
    inner_pad_x = (1 * mm) +6  # very small horizontal padding for separation
    inner_pad_top = 1 * mm  # tighter top inner padding
    inner_pad_bottom = 1 * mm  # tighter bottom inner padding

    resp = HttpResponse(content_type='application/pdf')
    resp['Content-Disposition'] = 'attachment; filename="barcodes.pdf"'
    c = canvas.Canvas(resp, pagesize=A4)
    c.setTitle("Barcode Labels")

    per_page = cols * rows

    for i, (p, code_val) in enumerate(items):
        cell = i % per_page
        row = cell // cols
        col = cell % cols

        if i and cell == 0:
            c.showPage()

        lx = left_margin + col * label_w
        ly = page_h - top_margin - (row + 1) * label_h

        c.saveState()
        c.setLineWidth(0.6)
        c.setDash(1, 2)
        inset = 1 * mm  # fixed inset for rectangle inside the cell
        c.rect(lx + inset, ly + inset+20, label_w - 2 * inset, 60, stroke=1, fill=0)
        c.restoreState()

        name_font, name_size = 'Helvetica', 8
        name_max_w = label_w - 2 * inner_pad_x
        name_lines = simpleSplit(p.name or '', name_font, name_size, name_max_w)[:2]

        c.setFont(name_font, name_size)
        y_text = ly + label_h - inner_pad_top - name_size
        for line in name_lines:
            c.drawCentredString(lx + label_w / 2.0, y_text, line)
            y_text -= (name_size + 1)

        # minimal gap between product name and top of bars
        barcode_top = y_text - 1
        min_bar_h = 10 * mm
        # place bars as high as possible under the name while respecting bottom padding
        barcode_height = 30
        bar_y = max(ly + inner_pad_bottom + 2, barcode_top - barcode_height)

        try:
            if sym == 'ean13' and _ean13_normalize(code_val):
                code_norm = _ean13_normalize(code_val)
                d = createBarcodeDrawing('EAN13', value=code_norm, barHeight=barcode_height, humanReadable=False)
                # Remove EAN-13 quiet zones to eliminate left/right padding inside the box
                try:
                    if hasattr(d, 'barcode'):
                        if hasattr(d.barcode, 'quiet'):
                            d.barcode.quiet = False
                        if hasattr(d.barcode, 'lquiet'):
                            d.barcode.lquiet = 0
                        if hasattr(d.barcode, 'rquiet'):
                            d.barcode.rquiet = 0
                        if hasattr(d.barcode, 'quietZone'):
                            d.barcode.quietZone = 0
                except Exception:
                    pass
                scale = ((label_w - 2 * inner_pad_x) / float(d.width)) if d.width else 1.0
                c.saveState()
                c.translate(lx + inner_pad_x, bar_y)
                c.scale(scale, 1.0)
                renderPDF.draw(d, c, 0, 0)
                c.restoreState()
                c.setFont('Helvetica', 8)
                text_y = max(ly + 1, bar_y - 8)
                c.drawCentredString(lx + label_w / 2.0, text_y, code_norm)
            else:
                # Remove Code128 quiet zones to eliminate left/right padding inside the box
                b = code128.Code128(str(code_val), barHeight=barcode_height, barWidth=0.2, quiet=False)
                bw = float(b.width)
                scale = ((label_w - 2 * inner_pad_x) / bw) if bw else 1.0
                c.saveState()
                c.translate(lx + inner_pad_x, bar_y)
                c.scale(scale, 1.0)
                b.drawOn(c, 0, 0)
                c.restoreState()
                c.setFont('Helvetica', 8)
                text_y = max(ly + 1, bar_y - 8)
                c.drawCentredString(lx + label_w / 2.0, text_y, str(code_val))
        except Exception:
            c.setFont('Helvetica', 8)
            c.drawCentredString(lx + label_w / 2.0, ly + label_h / 2.0, str(code_val))

    c.save()
    return resp

# --- Bulk stock adjust (CSV) ---
@login_required
@permission_required('posapp.can_adjust_stock', raise_exception=True)
def stock_bulk_adjust(request):
    """
    Upload a CSV to adjust stock in bulk.

    Accepts headers (case-insensitive):
      - code OR barcode  (identify product, at least one required)
      - delta            (signed int; add/remove this quantity)
      - new_stock        (int; set absolute stock; aliases: set_to, target)
      - note             (optional)
    """
    from .models import Product, StockMove  # local to avoid import cycles
    tenant = _tenant(request)
    if tenant.business_type == 'pharmacy':
        messages.info(request, 'Pharmacy stock must be managed through purchase batches.')
        return redirect('purchase_create')

    # Optional: sample CSV
    if request.GET.get('sample'):
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="stock_adjust_sample.csv"'
        resp.write('code,barcode,delta,new_stock,note\n')
        resp.write('PEN001,8901234567890,10,,Initial load\n')
        resp.write('NOTE001,,,-5,Damage write-off\n')
        return resp

    results, not_found, errors = [], [], []

    if request.method == 'POST' and 'file' in request.FILES:
        f = io.TextIOWrapper(request.FILES['file'].file, encoding='utf-8', newline='')
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            messages.error(request, 'CSV appears to have no header row.')
            return redirect('stock_bulk_adjust')

        headers = { (h or '').strip().lower(): h for h in reader.fieldnames }

        for row in reader:
            code = (row.get(headers.get('code','')) or '').strip() if 'code' in headers else ''
            barcode = (row.get(headers.get('barcode','')) or '').strip() if 'barcode' in headers else ''
            note = (row.get(headers.get('note','')) or '').strip() if 'note' in headers else ''
            delta_raw = (row.get(headers.get('delta','')) or '').strip() if 'delta' in headers else ''

            new_stock_raw = None
            for k in ('new_stock','set_to','target'):
                if k in headers:
                    new_stock_raw = (row.get(headers.get(k,'')) or '').strip()
                    if new_stock_raw:
                        break

            # find product
            p = None
            if code:
                p = Product.objects.filter(tenant=tenant, code=code).first()
            if not p and barcode:
                p = Product.objects.filter(tenant=tenant, barcode=barcode).first()
            if not p:
                not_found.append({'code': code, 'barcode': barcode})
                continue

            # current stock
            stock_now = p.stockmove_set.aggregate(s=Sum('change'))['s'] or 0

            # compute change
            change = None
            if delta_raw:
                try:
                    change = int(float(delta_raw))
                except Exception:
                    errors.append({'code': p.code, 'barcode': p.barcode, 'error': f'Invalid delta: {delta_raw}'})
                    continue
            elif new_stock_raw:
                try:
                    target = int(float(new_stock_raw))
                except Exception:
                    errors.append({'code': p.code, 'barcode': p.barcode, 'error': f'Invalid new_stock: {new_stock_raw}'})
                    continue
                change = target - stock_now
            else:
                # nothing to do on this row
                continue

            if change == 0:
                results.append({'code': p.code, 'barcode': p.barcode, 'old': stock_now, 'change': 0, 'new': stock_now, 'note': note})
                continue

            StockMove.objects.create(tenant=tenant, product=p, change=change, reason='adjustment', ref=(note or 'CSV bulk')[:64])
            new_qty = stock_now + change
            results.append({'code': p.code, 'barcode': p.barcode, 'old': stock_now, 'change': change, 'new': new_qty, 'note': note})

        return render(request, 'products/stock_bulk_adjust.html', {
            'results': results,
            'not_found': not_found,
            'errors': errors,
        })

    # GET: render upload form
    return render(request, 'products/stock_bulk_adjust.html')

# --- Bulk stock CSV template (download) ---
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse
import csv, io

@login_required
@permission_required('posapp.can_adjust_stock', raise_exception=True)
def stock_bulk_template(request):
    """
    Download a CSV template for bulk stock adjustments.

    Columns:
      code, barcode, delta, new_stock, note

    - Provide either code or barcode to identify the product.
    - Use 'delta' to add/remove units (signed integer).
    - Or use 'new_stock' to set absolute stock (integer).
    - 'note' is optional.
    """
    tenant = _tenant(request)
    if tenant.business_type == 'pharmacy':
        messages.info(request, 'Pharmacy stock must be managed through purchase batches.')
        return redirect('purchase_create')

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['code','barcode','delta','new_stock','note'])
    w.writerow(['PEN001','8901234567890','10','', 'Initial load'])
    w.writerow(['NOTE001','', '', '25', 'Set absolute stock to 25'])
    w.writerow(['GLUE10','', '-2','', 'Damaged / write-off'])
    data = buf.getvalue()
    buf.close()

    resp = HttpResponse(data, content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="stock_adjust_template.csv"'
    return resp

from django.http import FileResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required, permission_required
from pathlib import Path
from posapp.utils.backups import create_db_backup

@login_required
@permission_required('posapp.can_manage_settings', raise_exception=True)  # or a new 'can_backup_db'
def backup_download_now(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only platform administrators can download full database backups.")
    # Create a fresh backup and stream it
    fpath = create_db_backup()
    return FileResponse(
        open(fpath, "rb"),
        as_attachment=True,
        filename=fpath.name,
        content_type="application/gzip"
    )


@login_required
@permission_required('posapp.can_manage_settings', raise_exception=True)
def backup_restore_upload(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only platform administrators can restore full database backups.")
    """
    Accept a .gz database backup upload and write it over the current SQLite file.
    For safety, only supports SQLite in-app restore. Other engines should be restored externally.
    """
    from django.conf import settings as dj_settings
    if request.method != 'POST' or 'file' not in request.FILES:
        return HttpResponseForbidden('Upload a backup file (.sqlite3.gz)')

    db = dj_settings.DATABASES.get('default', {})
    engine = db.get('ENGINE', '')
    if 'sqlite' not in engine:
        return HttpResponseForbidden('In-app restore supported only for SQLite.')

    uploaded = request.FILES['file']
    # Basic validation: allow .gz files
    import gzip, shutil
    from pathlib import Path
    dst_path = Path(db['NAME']).resolve()

    # Make a safety copy of current DB
    try:
        if dst_path.exists():
            shutil.copyfile(dst_path, dst_path.with_suffix(dst_path.suffix + '.bak'))
    except Exception:
        pass

    # Write uploaded gzip content into the sqlite file
    with gzip.GzipFile(fileobj=uploaded.file, mode='rb') as gz, open(dst_path, 'wb') as out:
        shutil.copyfileobj(gz, out)

    messages.success(request, 'Database restored from uploaded backup. Please restart the app if required.')
    return redirect('settings_general')
