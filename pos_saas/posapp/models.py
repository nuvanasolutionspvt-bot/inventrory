from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings as dj_settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from decimal import Decimal
from django.utils import timezone

User = get_user_model()
TWO_DEC = Decimal('0.01')


# -------------------------------------------------------------------
# Base
# -------------------------------------------------------------------
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# -------------------------------------------------------------------
# Catalog
# -------------------------------------------------------------------
class Tenant(TimeStampedModel):
    PLAN_CHOICES = (
        ('trial', 'Free Trial'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
        ('starter', 'Starter'),
        ('growth', 'Growth'),
        ('enterprise', 'Enterprise'),
    )
    BUSINESS_TYPE_CHOICES = (
        ('retail_store', 'Retail Store'),
        ('wholesale', 'Wholesale'),
        ('pharmacy', 'Pharmacy'),
        ('restaurant', 'Restaurant'),
    )

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=80, unique=True, db_index=True)
    business_type = models.CharField(
        max_length=20,
        choices=BUSINESS_TYPE_CHOICES,
        default='retail_store',
    )
    owner_name = models.CharField(max_length=150)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=32)
    address = models.TextField()
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=80, default='India')
    tax_id = models.CharField(max_length=32, blank=True, default='')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='starter')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SubscriptionPlan(TimeStampedModel):
    PLAN_CHOICES = (
        ('trial', 'Free Trial'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    )

    code = models.CharField(max_length=20, choices=PLAN_CHOICES, unique=True)
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    duration_days = models.PositiveIntegerField(default=30)
    is_paid = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['price', 'duration_days']

    def __str__(self):
        return self.name


class TenantSubscription(TimeStampedModel):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    )

    tenant = models.ForeignKey(Tenant, related_name='subscriptions', on_delete=models.CASCADE)
    plan = models.ForeignKey(SubscriptionPlan, related_name='subscriptions', on_delete=models.PROTECT)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    razorpay_payment_id = models.CharField(max_length=120, blank=True, default='')
    razorpay_order_id = models.CharField(max_length=120, blank=True, default='')

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'status', 'ends_at']),
        ]
        ordering = ['-ends_at']

    @property
    def is_current(self):
        return self.status == 'active' and self.ends_at >= timezone.now()

    def __str__(self):
        return f"{self.tenant} - {self.plan} until {self.ends_at:%Y-%m-%d}"


class SubscriptionPaymentOrder(TimeStampedModel):
    STATUS_CHOICES = (
        ('created', 'Created'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
    )

    tenant = models.ForeignKey(Tenant, related_name='subscription_orders', on_delete=models.CASCADE)
    plan = models.ForeignKey(SubscriptionPlan, related_name='payment_orders', on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default='INR')
    razorpay_order_id = models.CharField(max_length=120, unique=True)
    razorpay_payment_id = models.CharField(max_length=120, blank=True, default='')
    razorpay_signature = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='created')

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['razorpay_order_id']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tenant} {self.plan} {self.status}"


class TenantMembership(TimeStampedModel):
    ROLE_CHOICES = (
        ('owner', 'Owner'),
        ('admin', 'Admin'),
        ('staff', 'Staff'),
    )

    tenant = models.ForeignKey(Tenant, related_name='memberships', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='tenant_memberships', on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [('tenant', 'user')]
        indexes = [
            models.Index(fields=['tenant', 'role']),
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"


class Category(TimeStampedModel):
    tenant = models.ForeignKey(Tenant, related_name='categories', on_delete=models.CASCADE)
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='uniq_tenant_category_name'),
        ]
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    tenant = models.ForeignKey(Tenant, related_name='products', on_delete=models.CASCADE)
    code = models.CharField(max_length=64)
    barcode = models.CharField(max_length=64, null=True, blank=True)
    name = models.CharField(max_length=200)
    image = models.ImageField(upload_to='product_images/%Y/%m/', blank=True, null=True)
    batch_no = models.CharField(max_length=64, default='')
    manufacture_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField(null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    full_available = models.BooleanField(default=True)
    half_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # per-product GST/VAT%
    reorder_level = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'code'], name='uniq_tenant_product_code'),
            models.UniqueConstraint(
                fields=['tenant', 'barcode'],
                condition=models.Q(barcode__isnull=False) & ~models.Q(barcode=''),
                name='uniq_tenant_product_barcode',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'is_active'], name='posapp_prod_tenant__12c615_idx'),
            models.Index(fields=['tenant', 'name'], name='posapp_prod_tenant__e1db27_idx'),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def stock(self):
        # total on-hand = sum of StockMove.change
        agg = self.stockmove_set.aggregate(total=models.Sum('change'))
        return agg['total'] or 0


class ProductBatch(TimeStampedModel):
    """Batch-level inventory record for pharmacy products."""

    STATUS_ACTIVE = 'active'
    STATUS_EXPIRED = 'expired'
    STATUS_EMPTY = 'empty'
    STATUS_CHOICES = (
        (STATUS_ACTIVE, 'Active'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_EMPTY, 'Empty'),
    )

    tenant = models.ForeignKey(
        Tenant,
        related_name='product_batches',
        on_delete=models.CASCADE,
        verbose_name='Business',
    )
    product = models.ForeignKey(
        Product,
        related_name='batches',
        on_delete=models.CASCADE,
    )
    supplier = models.ForeignKey(
        'Supplier',
        related_name='product_batches',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    batch_no = models.CharField(max_length=64, verbose_name='Batch number')
    manufacture_date = models.DateField(verbose_name='Manufacture date')
    expiry_date = models.DateField(verbose_name='Expiry date')
    purchase_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Purchase rate',
    )
    sale_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Sale price',
    )
    mrp = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='MRP',
    )
    received_qty = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='Received quantity',
    )
    available_qty = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='Available quantity',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    notes = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Product batch'
        verbose_name_plural = 'Product batches'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'product', 'batch_no'],
                name='uniq_tenant_product_batch_no',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant'], name='posapp_bat_tenant_1f413b_idx'),
            models.Index(fields=['product'], name='posapp_bat_product_17079d_idx'),
            models.Index(fields=['batch_no'], name='posapp_bat_batch_39a6e9_idx'),
            models.Index(fields=['expiry_date'], name='posapp_bat_expiry_8ec7df_idx'),
            models.Index(fields=['status'], name='posapp_bat_status_16aa98_idx'),
        ]
        ordering = ['expiry_date', 'batch_no']

    def __str__(self):
        return f"{self.product.code} - {self.product.name} / {self.batch_no}"

    def clean(self):
        errors = {}
        batch_no = (self.batch_no or '').strip()

        if not batch_no:
            errors['batch_no'] = 'Batch number cannot be blank.'
        else:
            self.batch_no = batch_no

        if self.tenant_id and self.tenant.business_type != 'pharmacy':
            errors['tenant'] = 'Product batches are available only for pharmacy businesses.'

        if self.product_id and self.tenant_id and self.product.tenant_id != self.tenant_id:
            errors['product'] = 'Product must belong to the same business as this batch.'

        if self.supplier_id and self.tenant_id and self.supplier.tenant_id != self.tenant_id:
            errors['supplier'] = 'Supplier must belong to the same business as this batch.'

        if self.manufacture_date and self.expiry_date and self.expiry_date <= self.manufacture_date:
            errors['expiry_date'] = 'Expiry date must be after manufacture date.'

        if errors:
            raise ValidationError(errors)

    def is_expired(self):
        """Return True when this batch is past its expiry date."""
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    def days_to_expiry(self):
        """Return days remaining until expiry; negative means already expired."""
        if not self.expiry_date:
            return None
        return (self.expiry_date - timezone.localdate()).days

    def stock_available(self):
        """Return True when this batch still has sellable stock."""
        return self.available_qty > 0 and not self.is_expired()

    def get_status(self):
        """Derive the current batch status from expiry and available quantity."""
        if self.is_expired():
            return self.STATUS_EXPIRED
        if self.available_qty <= 0:
            return self.STATUS_EMPTY
        return self.STATUS_ACTIVE

    def save(self, *args, **kwargs):
        self.status = self.get_status()
        self.full_clean()
        super().save(*args, **kwargs)


class ProductSet(TimeStampedModel):
    tenant = models.ForeignKey(Tenant, related_name='product_sets', on_delete=models.CASCADE)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'code'], name='uniq_tenant_product_set_code'),
        ]
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def stock(self):
        items = self.items.select_related('product')
        possible = []
        for item in items:
            if item.qty <= 0:
                continue
            possible.append(item.product.stock // item.qty)
        return min(possible) if possible else 0


class ProductSetItem(models.Model):
    product_set = models.ForeignKey(ProductSet, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        unique_together = [('product_set', 'product')]

    def __str__(self):
        return f"{self.product_set} / {self.product} x {self.qty}"


# -------------------------------------------------------------------
# Parties
# -------------------------------------------------------------------
class Supplier(TimeStampedModel):
    tenant = models.ForeignKey(Tenant, related_name='suppliers', on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'name'], name='posapp_supp_tenant__0730e0_idx'),
        ]

    def __str__(self):
        return self.name


class Customer(TimeStampedModel):
    tenant = models.ForeignKey(Tenant, related_name='customers', on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)

    # --- Credit profile ---
    sms_opt_in   = models.BooleanField(default=True)
    call_opt_in  = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'name'], name='posapp_cust_tenant__5dd38d_idx'),
            models.Index(fields=['tenant', 'phone'], name='posapp_cust_tenant__9f14e4_idx'),
        ]

    def __str__(self):
        return self.name

    # --- Credit helpers ---
    @property
    def balance(self) -> Decimal:
        """Outstanding amount (what customer owes us): debits - credits."""
        agg = self.customerledger_set.aggregate(
            d=models.Sum('debit'),
            c=models.Sum('credit'),
        )
        d = Decimal(agg.get('d') or 0)
        c = Decimal(agg.get('c') or 0)
        return (d - c).quantize(TWO_DEC)

    @property
    def available_credit(self) -> Decimal:
        """Credit is unlimited; this remains for compatibility with old callers."""
        return Decimal('0.00')

    @property
    def is_over_limit(self) -> bool:
        return False

    def threshold_reached(self) -> bool:
        return False


# -------------------------------------------------------------------
# Inbound (Purchases)
# -------------------------------------------------------------------
class Purchase(TimeStampedModel):
    tenant = models.ForeignKey(Tenant, related_name='purchases', on_delete=models.CASCADE)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField()
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'date'], name='posapp_purc_tenant__798ec0_idx'),
        ]

    def __str__(self):
        return f"PO-{self.id} {self.date}"


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_batch = models.ForeignKey(
        ProductBatch,
        related_name='purchase_items',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    qty = models.PositiveIntegerField()
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product} x {self.qty}"


# -------------------------------------------------------------------
# Outbound (Sales / Returns)
# -------------------------------------------------------------------
class Sale(TimeStampedModel):
    tenant = models.ForeignKey(Tenant, related_name='sales', on_delete=models.CASCADE)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField()
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # pre-tax
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)       # computed from per-product tax
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    PAYMENT_CHOICES = (('cash', 'Cash'), ('card', 'Card'), ('upi', 'UPI'), ('other', 'Other'))
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default='cash')

    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    is_return = models.BooleanField(default=False)  # credit note; totals stored as negative

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'date'], name='posapp_sale_tenant__88f376_idx'),
            models.Index(fields=['tenant', 'is_return'], name='posapp_sale_tenant__960f03_idx'),
        ]

    def __str__(self):
        return f"{'CRN' if self.is_return else 'INV'}-{self.id} {self.date}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True)
    product_batch = models.ForeignKey(
        ProductBatch,
        related_name='sale_items',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    product_set = models.ForeignKey(ProductSet, on_delete=models.PROTECT, null=True, blank=True)
    description = models.CharField(max_length=240, blank=True)
    details = models.TextField(blank=True)
    qty = models.IntegerField()  # negative for returns
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)  # pre-tax
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.description or self.product or self.product_set} x {self.qty}"


# -------------------------------------------------------------------
# Stock movements
# -------------------------------------------------------------------
class StockMove(TimeStampedModel):
    REASONS = (
        ('purchase', 'Purchase'),
        ('sale', 'Sale'),
        ('adjustment', 'Adjustment'),
        ('return', 'Return'),
    )
    tenant = models.ForeignKey(Tenant, related_name='stock_moves', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    change = models.IntegerField(help_text="Positive for inbound, negative for outbound")
    reason = models.CharField(max_length=20, choices=REASONS)
    ref = models.CharField(max_length=64, blank=True, help_text="Reference id (e.g. INV-12, CRN-2, PO-5)")

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'reason'], name='posapp_stoc_tenant__db2e44_idx'),
            models.Index(fields=['tenant', 'ref'], name='posapp_stoc_tenant__7b3064_idx'),
        ]

    def __str__(self):
        return f"{self.product} {self.change} ({self.reason})"


# -------------------------------------------------------------------
# App-level permissions anchor (no DB table)
# -------------------------------------------------------------------
class AppPermission(models.Model):
    """Anchor model to hold app-wide custom permissions (no table)."""
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ('can_pos', 'Can use POS (sell/return)'),
            ('can_view_reports', 'Can view and download reports'),
            ('can_print_barcodes', 'Can print barcode labels'),
            ('can_adjust_stock', 'Can adjust stock (quick/bulk)'),
            ('can_manage_purchases', 'Can create purchases'),
            ('can_manage_settings', 'Can manage POS settings'),
            ('can_manage_users', 'Can manage users and roles'),
            ('can_credit_receive', 'Can receive customer payments'),
            ('can_credit_charge',  'Can post customer charges/fees'),
            ('can_credit_view',    'Can view customer credit statements'),
        ]


# -------------------------------------------------------------------
# Credit Ledger
# -------------------------------------------------------------------
class CustomerLedger(models.Model):
    tenant      = models.ForeignKey(Tenant, related_name='customer_ledger_lines', on_delete=models.CASCADE)
    customer    = models.ForeignKey('Customer', on_delete=models.CASCADE)
    date        = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=200, blank=True)
    debit       = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # increases balance
    credit      = models.DecimalField(max_digits=12, decimal_places=2, default=0) # decreases balance
    sale        = models.ForeignKey('Sale', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-date','-id']
        indexes = [
            models.Index(fields=['tenant', 'date'], name='posapp_cust_tenant__286454_idx'),
            models.Index(fields=['customer', 'date']),
            models.Index(fields=['sale']),
        ]

    def __str__(self):
        amt = self.debit or self.credit
        side = 'DR' if self.debit else 'CR'
        return f"{self.customer} {side} {amt} on {self.date}"


# -------------------------------------------------------------------
# Site settings (singleton)
# -------------------------------------------------------------------
class SiteSetting(models.Model):
    PRINTER_TYPE_CHOICES = [
        ('a4', 'A4 / Normal Printer'),
        ('thermal_80', 'Thermal Receipt 80mm'),
        ('thermal_58', 'Thermal Receipt 58mm'),
        ('label', 'Barcode Label Printer'),
    ]

    singleton_id = models.PositiveIntegerField(primary_key=True, default=1, editable=False)
    tenant = models.OneToOneField(Tenant, related_name='site_setting', on_delete=models.CASCADE)

    # Org / Bill
    org_name    = models.CharField(max_length=120, default='Your Store')
    org_address = models.TextField(blank=True, default='')
    org_phone   = models.CharField(max_length=32, blank=True, default='')
    org_email   = models.EmailField(blank=True, default='')
    bill_title  = models.CharField(max_length=60, default='Tax Invoice')
    bill_footer = models.CharField(max_length=200, blank=True, default='')
    bill_tax_inclusive = models.BooleanField(default=True)
    restaurant_menu_tax_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    printer_type = models.CharField(max_length=20, choices=PRINTER_TYPE_CHOICES, default='a4')
    payment_qr = models.ImageField(upload_to='tenant_qr/%Y/%m/', blank=True, null=True)

    # SMS
    sms_enabled  = models.BooleanField(default=False)
    sms_provider = models.CharField(max_length=20, choices=[
        ('textlocal', 'Textlocal'),
        ('msg91', 'MSG91'),
    ], default='textlocal')
    sms_api_key  = models.CharField(max_length=200, blank=True, default='')
    sms_sender   = models.CharField(max_length=11, blank=True, default='TXTLCL')  # 6-11 chars as per DLT

    # Calls (optional)
    call_enabled = models.BooleanField(default=False)
    call_provider = models.CharField(max_length=20, choices=[
        ('twilio', 'Twilio'),
    ], default='twilio')
    call_sid     = models.CharField(max_length=64, blank=True, default='')
    call_token   = models.CharField(max_length=64, blank=True, default='')
    call_from    = models.CharField(max_length=20, blank=True, default='')

    class Meta:
        permissions = [
            ('can_manage_settings', 'Can manage POS settings'),
        ]

    def __str__(self):
        return f"Settings ({self.org_name})"

    @staticmethod
    def get(tenant=None):
        if tenant is None:
            tenant = Tenant.objects.order_by('id').first()
        if tenant is None:
            tenant = Tenant.objects.create(
                name='Default Store',
                slug='default-store',
                owner_name='Admin',
                contact_email='admin@example.com',
                contact_phone='',
                address='',
                city='',
                state='',
                postal_code='',
            )
        obj, _ = SiteSetting.objects.get_or_create(
            tenant=tenant,
            defaults={'singleton_id': tenant.pk},
        )
        return obj


# Ensure the singleton exists right after migrations
@receiver(post_migrate)
def ensure_settings_singleton(sender, **kwargs):
    if sender.label == __name__.split('.')[0]:  # only for this app
        default_plans = [
            ('trial', 'Free Trial', Decimal('0.00'), 7, False),
            ('monthly', 'Monthly Plan', Decimal('299.00'), 30, True),
            ('yearly', 'Yearly Plan', Decimal('3500.00'), 365, True),
        ]
        for code, name, price, days, is_paid in default_plans:
            SubscriptionPlan.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'price': price,
                    'duration_days': days,
                    'is_paid': is_paid,
                    'is_active': True,
                },
            )
        for tenant in Tenant.objects.all():
            SiteSetting.get(tenant)
