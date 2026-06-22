from django.contrib import admin
from .models import (
    Category, Product, ProductSet, ProductSetItem, Supplier, Customer, Purchase,
    PurchaseItem, Sale, SaleItem, StockMove, Tenant, TenantMembership,
    SiteSetting, CustomerLedger, SubscriptionPlan, TenantSubscription,
    SubscriptionPaymentOrder
)


class TenantMembershipInline(admin.TabularInline):
    model = TenantMembership
    extra = 0
    raw_id_fields = ['user']


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'business_type', 'owner_name', 'contact_email', 'contact_phone', 'plan', 'is_active']
    list_filter = ['business_type', 'plan', 'is_active', 'country', 'state']
    search_fields = ['name', 'slug', 'owner_name', 'contact_email', 'contact_phone', 'tax_id']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [TenantMembershipInline]


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'user', 'role', 'is_active']
    list_filter = ['role', 'is_active']
    raw_id_fields = ['tenant', 'user']
    search_fields = ['tenant__name', 'tenant__slug', 'user__username', 'user__email']


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'price', 'duration_days', 'is_paid', 'is_active']
    list_filter = ['is_paid', 'is_active']
    search_fields = ['code', 'name']


@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'plan', 'status', 'starts_at', 'ends_at', 'razorpay_payment_id']
    list_filter = ['status', 'plan']
    search_fields = ['tenant__name', 'tenant__slug', 'razorpay_payment_id', 'razorpay_order_id']
    raw_id_fields = ['tenant']


@admin.register(SubscriptionPaymentOrder)
class SubscriptionPaymentOrderAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'plan', 'amount', 'currency', 'status', 'razorpay_order_id', 'razorpay_payment_id']
    list_filter = ['status', 'plan', 'currency']
    search_fields = ['tenant__name', 'tenant__slug', 'razorpay_order_id', 'razorpay_payment_id']
    raw_id_fields = ['tenant']

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant']
    list_filter = ['tenant']
    search_fields = ['name', 'tenant__name', 'tenant__slug']

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['code','barcode','name','tenant','batch_no','manufacture_date','expiry_date','category','unit_price','cost_price','tax_percent','stock','is_active']
    list_filter = ['tenant','category','is_active']
    search_fields = ['code','barcode','name','batch_no','tenant__name','tenant__slug']

class ProductSetItemInline(admin.TabularInline):
    model = ProductSetItem
    extra = 1

@admin.register(ProductSet)
class ProductSetAdmin(admin.ModelAdmin):
    list_display = ['code','name','tenant','unit_price','tax_percent','stock','is_active']
    list_filter = ['tenant','is_active']
    search_fields = ['code','name','tenant__name','tenant__slug']
    inlines = [ProductSetItemInline]

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant', 'phone', 'email']
    list_filter = ['tenant']
    search_fields = ['name','phone','email','tenant__name','tenant__slug']

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant', 'phone', 'email', 'credit_limit']
    list_filter = ['tenant']
    search_fields = ['name','phone','email','tenant__name','tenant__slug']

class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ['id','tenant','supplier','date','total']
    list_filter = ['tenant','date']
    inlines = [PurchaseItemInline]

class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['id','tenant','customer','date','total','payment_method','paid_amount','is_return']
    list_filter = ['tenant','date','is_return']
    inlines = [SaleItemInline]

@admin.register(StockMove)
class StockMoveAdmin(admin.ModelAdmin):
    list_display = ['tenant','product','change','reason','ref','created_at']
    list_filter = ['tenant','reason']


@admin.register(CustomerLedger)
class CustomerLedgerAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'customer', 'date', 'description', 'debit', 'credit', 'sale']
    list_filter = ['tenant', 'date']
    search_fields = ['customer__name', 'description', 'tenant__name', 'tenant__slug']


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'org_name', 'org_phone', 'org_email', 'printer_type']
    search_fields = ['tenant__name', 'tenant__slug', 'org_name', 'org_phone', 'org_email']
