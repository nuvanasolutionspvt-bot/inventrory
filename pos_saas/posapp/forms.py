from datetime import datetime, time, timedelta
from decimal import Decimal
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    Product, ProductSet, Category, Supplier, Customer,
    Purchase, Sale, SiteSetting, Tenant, TenantMembership,
    SubscriptionPlan, TenantSubscription
)

# ---------------------------
# Auth / Security Forms
# ---------------------------

class CompanyAuthenticationForm(AuthenticationForm):
    """Only platform superusers may authenticate through the company portal."""

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise ValidationError(
                "Please enter a correct username and password.",
                code="invalid_login",
            )


RESERVED_TENANT_SLUGS = {
    'admin', 'api', 'auth', 'backup', 'credit', 'customers', 'dashboard',
    'invoice', 'login', 'logout', 'pos', 'products', 'purchases', 'register',
    'reports', 'sales', 'security', 'settings', 'static', 'subscription', 'suppliers',
}


class TenantRegistrationForm(forms.Form):
    business_name = forms.CharField(
        max_length=150,
        label="Business name",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "organization",
            "placeholder": "Store or company name",
        }),
    )
    account_slug = forms.SlugField(
        max_length=80,
        label="Account ID",
        help_text="Used as the tenant workspace identifier.",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "off",
            "placeholder": "my-store",
        }),
    )
    business_type = forms.ChoiceField(
        choices=Tenant.BUSINESS_TYPE_CHOICES,
        label="Business type",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    subscription_plan = forms.ChoiceField(
        choices=(
            ('trial', 'Free Trial - 7 days'),
            ('monthly', 'Monthly - Rs. 299'),
            ('yearly', 'Yearly - Rs. 3500'),
        ),
        initial='trial',
        label="Subscription plan",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    owner_name = forms.CharField(
        max_length=150,
        label="Owner name",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "name",
            "placeholder": "Full name",
        }),
    )
    contact_email = forms.EmailField(
        label="Business email",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "autocomplete": "email",
            "placeholder": "owner@example.com",
        }),
    )
    contact_phone = forms.CharField(
        max_length=32,
        label="Business phone",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "tel",
            "placeholder": "+91 98765 43210",
        }),
    )
    tax_id = forms.CharField(
        max_length=32,
        required=False,
        label="GSTIN / Tax ID",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "off",
            "placeholder": "Optional",
        }),
    )
    address = forms.CharField(
        label="Business address",
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 2,
            "autocomplete": "street-address",
            "placeholder": "Billing and business address",
        }),
    )
    city = forms.CharField(
        max_length=80,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "address-level2",
            "placeholder": "City",
        }),
    )
    state = forms.CharField(
        max_length=80,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "address-level1",
            "placeholder": "State",
        }),
    )
    postal_code = forms.CharField(
        max_length=20,
        label="PIN / Postal code",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "postal-code",
            "placeholder": "Postal code",
        }),
    )
    country = forms.CharField(
        max_length=80,
        initial="India",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "country-name",
            "placeholder": "Country",
        }),
    )
    username = forms.CharField(
        max_length=150,
        label="Admin username",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "username",
            "placeholder": "Login username",
        }),
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "new-password",
            "placeholder": "Create a password",
        }),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "new-password",
            "placeholder": "Repeat password",
        }),
    )
    accepted_terms = forms.BooleanField(
        label="I confirm this information is correct and I am authorized to create this tenant.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_account_slug(self):
        slug = slugify(self.cleaned_data['account_slug']).lower()
        if not slug:
            raise ValidationError("Enter a valid account ID.")
        if slug in RESERVED_TENANT_SLUGS:
            raise ValidationError("This account ID is reserved. Choose another one.")
        if Tenant.objects.filter(slug__iexact=slug).exists():
            raise ValidationError("This account ID is already taken.")
        return slug

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("This username is already taken.")
        return username

    def clean_contact_email(self):
        email = self.cleaned_data['contact_email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("A user with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', "Passwords do not match.")
        if password1:
            try:
                validate_password(password1)
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned

    def _admin_group(self):
        group, created = Group.objects.get_or_create(name='Admin')
        if created or not group.permissions.exists():
            group.permissions.set(Permission.objects.filter(content_type__app_label='posapp'))
        return group

    @transaction.atomic
    def save(self):
        tenant = Tenant.objects.create(
            name=self.cleaned_data['business_name'],
            slug=self.cleaned_data['account_slug'],
            business_type=self.cleaned_data['business_type'],
            owner_name=self.cleaned_data['owner_name'],
            contact_email=self.cleaned_data['contact_email'],
            contact_phone=self.cleaned_data['contact_phone'],
            tax_id=self.cleaned_data.get('tax_id', ''),
            address=self.cleaned_data['address'],
            city=self.cleaned_data['city'],
            state=self.cleaned_data['state'],
            postal_code=self.cleaned_data['postal_code'],
            country=self.cleaned_data['country'],
            plan=self.cleaned_data['subscription_plan'],
        )
        name_parts = self.cleaned_data['owner_name'].split(None, 1)
        user = User.objects.create_user(
            username=self.cleaned_data['username'],
            email=self.cleaned_data['contact_email'],
            password=self.cleaned_data['password1'],
            first_name=name_parts[0],
            last_name=name_parts[1] if len(name_parts) > 1 else '',
            is_staff=False,
        )
        user.groups.add(self._admin_group())
        TenantMembership.objects.create(tenant=tenant, user=user, role='owner')
        SiteSetting.objects.get_or_create(
            tenant=tenant,
            defaults={
                'singleton_id': tenant.pk,
                'org_name': tenant.name,
                'org_address': tenant.address,
                'org_phone': tenant.contact_phone,
                'org_email': tenant.contact_email,
            },
        )
        plan = SubscriptionPlan.objects.filter(code=self.cleaned_data['subscription_plan'], is_active=True).first()
        if plan and plan.code == 'trial':
            now = timezone.now()
            TenantSubscription.objects.create(
                tenant=tenant,
                plan=plan,
                starts_at=now,
                ends_at=now + timedelta(days=plan.duration_days),
                status='active',
            )
        return tenant, user


class CompanyBusinessCreateForm(TenantRegistrationForm):
    accepted_terms = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.HiddenInput(),
    )
    subscription_expiry = forms.DateField(
        required=False,
        label="Expiry date",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        help_text="Optional. Use this to give custom access, for example one month for friends.",
    )

    @transaction.atomic
    def save(self):
        tenant, user = super().save()
        expiry = self.cleaned_data.get('subscription_expiry')
        plan = SubscriptionPlan.objects.filter(code=self.cleaned_data['subscription_plan'], is_active=True).first()
        if plan and expiry:
            subscription = (
                TenantSubscription.objects
                .filter(tenant=tenant, plan=plan, status='active')
                .order_by('-ends_at')
                .first()
            )
            ends_at = timezone.make_aware(datetime.combine(expiry, time.min), timezone.get_current_timezone())
            if subscription:
                subscription.ends_at = ends_at
                subscription.save(update_fields=['ends_at', 'updated_at'])
            else:
                TenantSubscription.objects.create(
                    tenant=tenant,
                    plan=plan,
                    starts_at=timezone.now(),
                    ends_at=ends_at,
                    status='active',
                )
        return tenant, user


class CompanyBusinessEditForm(forms.ModelForm):
    subscription_plan = forms.ChoiceField(
        choices=(
            ('trial', 'Free Trial - 7 days'),
            ('monthly', 'Monthly - Rs. 299'),
            ('yearly', 'Yearly - Rs. 3500'),
        ),
        label="Subscription plan",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    subscription_expiry = forms.DateField(
        required=False,
        label="Expiry date",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )

    class Meta:
        model = Tenant
        fields = [
            'name', 'business_type', 'owner_name', 'contact_email', 'contact_phone',
            'address', 'city', 'state', 'postal_code', 'country', 'tax_id', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={"class": "form-control"}),
            'business_type': forms.Select(attrs={"class": "form-select"}),
            'owner_name': forms.TextInput(attrs={"class": "form-control"}),
            'contact_email': forms.EmailInput(attrs={"class": "form-control"}),
            'contact_phone': forms.TextInput(attrs={"class": "form-control"}),
            'address': forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            'city': forms.TextInput(attrs={"class": "form-control"}),
            'state': forms.TextInput(attrs={"class": "form-control"}),
            'postal_code': forms.TextInput(attrs={"class": "form-control"}),
            'country': forms.TextInput(attrs={"class": "form-control"}),
            'tax_id': forms.TextInput(attrs={"class": "form-control"}),
            'is_active': forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        subscription = kwargs.pop('subscription', None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['subscription_plan'].initial = self.instance.plan if self.instance.plan in {'trial', 'monthly', 'yearly'} else 'trial'
        if subscription:
            self.fields['subscription_plan'].initial = subscription.plan.code
            self.fields['subscription_expiry'].initial = timezone.localtime(subscription.ends_at).date()

    @transaction.atomic
    def save(self, commit=True):
        tenant = super().save(commit=False)
        tenant.plan = self.cleaned_data['subscription_plan']
        if commit:
            tenant.save()
            plan = SubscriptionPlan.objects.filter(code=tenant.plan, is_active=True).first()
            expiry = self.cleaned_data.get('subscription_expiry')
            if plan and expiry:
                ends_at = timezone.make_aware(datetime.combine(expiry, time.min), timezone.get_current_timezone())
                subscription = (
                    TenantSubscription.objects
                    .filter(tenant=tenant, status='active')
                    .order_by('-ends_at')
                    .first()
                )
                if subscription:
                    subscription.plan = plan
                    subscription.ends_at = ends_at
                    subscription.save(update_fields=['plan', 'ends_at', 'updated_at'])
                else:
                    TenantSubscription.objects.create(
                        tenant=tenant,
                        plan=plan,
                        starts_at=timezone.now(),
                        ends_at=ends_at,
                        status='active',
                    )
        return tenant


class TenantModelFormMixin:
    tenant = None

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)

    def tenant_unique_exists(self, model, field_name, value):
        if not self.tenant or value in (None, ''):
            return False
        qs = model.objects.filter(tenant=self.tenant, **{f"{field_name}__iexact": value})
        if getattr(self, 'instance', None) is not None and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        return qs.exists()

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.tenant is not None and hasattr(obj, 'tenant_id') and not obj.tenant_id:
            obj.tenant = self.tenant
        if commit:
            obj.save()
            if hasattr(self, 'save_m2m'):
                self.save_m2m()
        return obj


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "new-password",
            "placeholder": "Set a password"
        }),
        label="Password"
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "new-password",
            "placeholder": "Confirm password"
        }),
        label="Confirm Password"
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by('name'),
        required=False,
        widget=forms.SelectMultiple(attrs={
            "class": "form-select js-enhance-select",
            "multiple": "multiple",
            "data-placeholder": "Assign groups/roles",
            "data-max-items": "50",
        }),
        label="Groups / Roles",
        help_text="Select one or more roles for this user."
    )
    is_staff = forms.BooleanField(initial=True, required=False, label="Staff access")
    is_active = forms.BooleanField(initial=True, required=False, label="Active")

    class Meta:
        model = User
        fields = ['username', 'email', 'is_staff', 'is_active', 'groups']
        widgets = {
            'username': forms.TextInput(attrs={
                "class": "form-control",
                "autocomplete": "username",
                "placeholder": "Login username"
            }),
            'email': forms.EmailInput(attrs={
                "class": "form-control",
                "autocomplete": "email",
                "placeholder": "name@example.com"
            }),
        }

    def clean(self):
        c = super().clean()
        if c.get('password1') != c.get('password2'):
            self.add_error('password2', "Passwords do not match.")
        return c

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
            self.save_m2m()
            user.groups.set(self.cleaned_data.get('groups', []))
        return user


class UserEditForm(forms.ModelForm):
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "new-password",
            "placeholder": "New password (optional)"
        }),
        required=False, label="New Password"
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "new-password",
            "placeholder": "Confirm new password"
        }),
        required=False, label="Confirm New Password"
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by('name'),
        required=False,
        widget=forms.SelectMultiple(attrs={
            "class": "form-select js-enhance-select",
            "multiple": "multiple",
            "data-placeholder": "Assign groups/roles",
            "data-max-items": "50",
        }),
        label="Groups / Roles"
    )
    is_staff = forms.BooleanField(required=False, label="Staff access")
    is_active = forms.BooleanField(required=False, label="Active")

    class Meta:
        model = User
        fields = ['email', 'is_staff', 'is_active', 'groups']
        widgets = {
            'email': forms.EmailInput(attrs={
                "class": "form-control",
                "autocomplete": "email",
                "placeholder": "name@example.com"
            }),
        }

    def clean(self):
        c = super().clean()
        p1, p2 = c.get('password1'), c.get('password2')
        if p1 or p2:
            if p1 != p2:
                self.add_error('password2', "Passwords do not match.")
        return c

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get('password1'):
            user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
            self.save_m2m()
            user.groups.set(self.cleaned_data.get('groups', []))
        return user


class RoleForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Role name (e.g., Cashier, Manager)"
            })
        }


class RolePermissionForm(forms.Form):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.filter(content_type__app_label='posapp').order_by('codename'),
        required=False,
        widget=forms.SelectMultiple(attrs={
            "class": "form-select js-enhance-select",
            "multiple": "multiple",
            "data-placeholder": "Select permissions",
            "data-max-items": "200",
        }),
        help_text="Attach permissions to this role."
    )

# ---------------------------
# Master Data Forms
# ---------------------------

class ProductForm(TenantModelFormMixin, forms.ModelForm):
    PHARMACY_FIELDS = ('batch_no', 'manufacture_date', 'expiry_date')
    PRODUCT_IMAGE_MAX_SIZE = 2 * 1024 * 1024
    PRODUCT_IMAGE_ALLOWED_TYPES = {'image/png', 'image/jpeg'}
    PRODUCT_IMAGE_ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg')

    class Meta:
        model = Product
        fields = [
            'code','barcode','name','image','batch_no','manufacture_date','expiry_date',
            'category','unit_price','full_available','half_price','cost_price','tax_percent','reorder_level','is_active'
        ]
        widgets = {
            'code': forms.TextInput(attrs={"class": "form-control", "autofocus": "autofocus", "placeholder": "Unique code (e.g., PEN-001)"}),
            'barcode': forms.TextInput(attrs={"class": "form-control", "placeholder": "EAN-13 / Code128 / custom"}),
            'name': forms.TextInput(attrs={"class": "form-control", "placeholder": "Product name"}),
            'image': forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/png,image/jpeg"}),
            'batch_no': forms.TextInput(attrs={"class": "form-control", "placeholder": "Batch number"}),
            'manufacture_date': forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            'expiry_date': forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            'category': forms.Select(attrs={
                "class": "form-select js-enhance-select",
                "data-allow-clear": "true",
                "data-placeholder": "Select a category",
            }),
            'unit_price': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "Selling price"}),
            'full_available': forms.CheckboxInput(attrs={"class": "form-check-input"}),
            'half_price': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01", "placeholder": "Half price"}),
            'cost_price': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "Cost price"}),
            'tax_percent': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "GST %"}),
            'reorder_level': forms.NumberInput(attrs={"class": "form-control", "min": "0", "placeholder": "Warn at qty"}),
            'is_active': forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.tenant is not None:
            self.fields['category'].queryset = Category.objects.filter(tenant=self.tenant).order_by('name')
        else:
            self.fields['category'].queryset = Category.objects.none()
        # Product-level batch fields are legacy fields. Pharmacy batch details
        # are managed through purchases/ProductBatch, while other business
        # types retain their existing behavior of not exposing these fields.
        for field_name in self.PHARMACY_FIELDS:
            self.fields.pop(field_name, None)
        if self.is_restaurant_tenant:
            self.fields['unit_price'].label = 'Full Price'
            self.fields['half_price'].required = False
        else:
            self.fields.pop('image', None)
            self.fields.pop('full_available', None)
            self.fields.pop('half_price', None)

    @property
    def is_pharmacy_tenant(self):
        return bool(self.tenant and self.tenant.business_type == 'pharmacy')

    @property
    def is_restaurant_tenant(self):
        return bool(self.tenant and self.tenant.business_type == 'restaurant')

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not self.is_pharmacy_tenant:
            obj.batch_no = ''
            obj.expiry_date = None
        if not self.is_restaurant_tenant:
            obj.full_available = True
            obj.half_price = None
        if commit:
            obj.save()
            self.save_m2m()
        return obj

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if self.tenant_unique_exists(Product, 'code', code):
            raise ValidationError("This product code already exists for this tenant.")
        return code

    def clean_barcode(self):
        barcode = (self.cleaned_data.get('barcode') or '').strip() or None
        if barcode and self.tenant_unique_exists(Product, 'barcode', barcode):
            raise ValidationError("This barcode already exists for this tenant.")
        return barcode

    def clean(self):
        cleaned = super().clean()
        if self.is_restaurant_tenant:
            full_available = cleaned.get('full_available')
            half_price = cleaned.get('half_price')
            if half_price is not None and half_price <= 0:
                self.add_error('half_price', "Half price must be greater than zero.")
            if not full_available and half_price is None:
                self.add_error('half_price', "Enter a half price when full portion is not available.")
        return cleaned

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image:
            return image
        if not self.is_restaurant_tenant:
            raise ValidationError("Product images are available only for restaurant businesses.")
        if not hasattr(image, 'content_type'):
            return image
        if getattr(image, 'size', 0) > self.PRODUCT_IMAGE_MAX_SIZE:
            raise ValidationError("Product image must be 2 MB or smaller.")
        content_type = getattr(image, 'content_type', '')
        name = getattr(image, 'name', '').lower()
        if content_type not in self.PRODUCT_IMAGE_ALLOWED_TYPES or not name.endswith(self.PRODUCT_IMAGE_ALLOWED_EXTENSIONS):
            raise ValidationError("Upload a PNG or JPG image.")
        return image


class ProductSetForm(TenantModelFormMixin, forms.ModelForm):
    class Meta:
        model = ProductSet
        fields = ['code', 'name', 'unit_price', 'tax_percent', 'is_active']
        widgets = {
            'code': forms.TextInput(attrs={"class": "form-control", "autofocus": "autofocus", "placeholder": "Unique code (e.g., CLASS10-SET)"}),
            'name': forms.TextInput(attrs={"class": "form-control", "placeholder": "Set name (e.g., 10 Class Set)"}),
            'unit_price': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "Selling price"}),
            'tax_percent': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "GST %"}),
            'is_active': forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if self.tenant_unique_exists(ProductSet, 'code', code):
            raise ValidationError("This set code already exists for this tenant.")
        return code


class CategoryForm(TenantModelFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={"class": "form-control", "placeholder": "Category name"})
        }

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if self.tenant_unique_exists(Category, 'name', name):
            raise ValidationError("This category already exists for this tenant.")
        return name


class SupplierForm(TenantModelFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name','phone','email']
        widgets = {
            'name': forms.TextInput(attrs={"class": "form-control", "placeholder": "Supplier name"}),
            'phone': forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone"}),
            'email': forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email"}),
        }


class CustomerForm(TenantModelFormMixin, forms.ModelForm):
    """Extended for credit system."""
    class Meta:
        model = Customer
        fields = ['name','phone','email','sms_opt_in','call_opt_in']
        widgets = {
            'name': forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer name"}),
            'phone': forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone"}),
            'email': forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email"}),
            'sms_opt_in': forms.CheckboxInput(attrs={"class": "form-check-input"}),
            'call_opt_in': forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

# ---------------------------
# Transactions
# ---------------------------

class PurchaseForm(TenantModelFormMixin, forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ['supplier','date','notes']
        widgets = {
            'supplier': forms.Select(attrs={
                "class": "form-select js-enhance-select",
                "data-allow-clear": "true",
                "data-placeholder": "Select supplier",
            }),
            'date': forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            'notes': forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Optional notes"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.tenant is not None:
            self.fields['supplier'].queryset = Supplier.objects.filter(tenant=self.tenant).order_by('name')
        else:
            self.fields['supplier'].queryset = Supplier.objects.none()


class SaleForm(TenantModelFormMixin, forms.ModelForm):
    is_return = forms.BooleanField(required=False, label='Return (Credit Note)')
    class Meta:
        model = Sale
        fields = ['customer','date','discount','payment_method','paid_amount','is_return']
        widgets = {
            'customer': forms.Select(attrs={
                "class": "form-select js-enhance-select",
                "data-allow-clear": "true",
                "data-placeholder": "Walk-in (leave empty) or pick customer",
            }),
            'date': forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            'discount': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "0.00"}),
            'payment_method': forms.Select(attrs={
                "class": "form-select js-enhance-select",
                "data-placeholder": "Payment method"
            }),
            'paid_amount': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "0.00"}),
            'is_return': forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.tenant is not None:
            self.fields['customer'].queryset = Customer.objects.filter(tenant=self.tenant).order_by('name')
        else:
            self.fields['customer'].queryset = Customer.objects.none()

# ---------------------------
# Stock
# ---------------------------

class StockAdjustForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        required=True,
        widget=forms.Select(attrs={
            "class": "form-select js-enhance-select",
            "data-allow-clear": "true",
            "data-placeholder": "Select product",
        })
    )
    qty = forms.IntegerField(
        min_value=1, initial=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
        help_text="Units to add to stock"
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional note"}),
        help_text="Optional note for this stock adjustment"
    )

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields['product'].queryset = Product.objects.filter(
                tenant=tenant,
                is_active=True,
            ).order_by('code')

# ---------------------------
# Settings
# ---------------------------

class SiteSettingForm(forms.ModelForm):
    class Meta:
        model = SiteSetting
        fields = [
            # Org/Bill
            'org_name','org_address','org_phone','org_email',
            'bill_title','bill_footer','bill_tax_inclusive','restaurant_menu_tax_percent','printer_type','payment_qr',
            # SMS
            'sms_enabled','sms_provider','sms_api_key','sms_sender',
            # Calls
            'call_enabled','call_provider','call_sid','call_token','call_from',
        ]
        widgets = {
            'org_name': forms.TextInput(attrs={"class": "form-control", "placeholder": "Your store name"}),
            'org_address': forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Address as shown on bill"}),
            'org_phone': forms.TextInput(attrs={"class": "form-control", "placeholder": "+91 …"}),
            'org_email': forms.EmailInput(attrs={"class": "form-control", "placeholder": "billing@store.com"}),

            'bill_title': forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., TAX INVOICE"}),
            'bill_footer': forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Footer note on invoices"}),
            'bill_tax_inclusive': forms.CheckboxInput(attrs={"class": "form-check-input"}),
            'restaurant_menu_tax_percent': forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100", "placeholder": "e.g. 5"}),
            'payment_qr': forms.ClearableFileInput(attrs={
                "class": "form-control", "accept": "image/png,image/jpeg,image/webp",
            }),
            'printer_type': forms.Select(attrs={
                "class": "form-select js-enhance-select",
                "data-placeholder": "Select printer type",
            }),

            'sms_enabled': forms.CheckboxInput(attrs={"class": "form-check-input"}),
            'sms_provider': forms.Select(attrs={
                "class": "form-select js-enhance-select",
                "data-placeholder": "Choose SMS provider"
            }),
            'sms_api_key': forms.TextInput(attrs={"class": "form-control", "placeholder": "API key / token"}),
            'sms_sender': forms.TextInput(attrs={"class": "form-control", "placeholder": "Sender ID (e.g., ACMECO)"}),

            'call_enabled': forms.CheckboxInput(attrs={"class": "form-check-input"}),
            'call_provider': forms.Select(attrs={
                "class": "form-select js-enhance-select",
                "data-placeholder": "Choose call provider"
            }),
            'call_sid': forms.TextInput(attrs={"class": "form-control", "placeholder": "Account SID"}),
            'call_token': forms.TextInput(attrs={"class": "form-control", "placeholder": "Auth token"}),
            'call_from': forms.TextInput(attrs={"class": "form-control", "placeholder": "Caller ID / From number"}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        if not (self.tenant and self.tenant.business_type == 'restaurant'):
            self.fields.pop('restaurant_menu_tax_percent', None)

    def clean_restaurant_menu_tax_percent(self):
        value = self.cleaned_data.get('restaurant_menu_tax_percent')
        if value is None:
            return value
        if value < Decimal('0') or value > Decimal('100'):
            raise ValidationError('Tax percentage must be between 0 and 100.')
        return value

    def clean_payment_qr(self):
        image = self.cleaned_data.get('payment_qr')
        if image and getattr(image, 'size', 0) > 2 * 1024 * 1024:
            raise ValidationError('QR image must be 2 MB or smaller.')
        return image

# ---------------------------
# Credit System – new utility forms
# ---------------------------

class ReceivePaymentForm(forms.Form):
    """
    Record a payment received from a customer.
    Will translate to a CustomerLedger CREDIT (reduces balance).
    """
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        widget=forms.Select(attrs={
            "class": "form-select js-enhance-select",
            "data-allow-clear": "true",
            "data-placeholder": "Select customer",
        })
    )
    date = forms.DateField(
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"})
    )
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01", "placeholder": "0.00"})
    )
    reference = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Txn Ref / UTR / Cheque #"})
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional note"})
    )

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields['customer'].queryset = Customer.objects.filter(tenant=tenant).order_by('name')


class CustomerChargeForm(forms.Form):
    """
    Post a manual charge (opening balance, fee, adjustment).
    Will translate to a CustomerLedger DEBIT (increases balance).
    """
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        widget=forms.Select(attrs={
            "class": "form-select js-enhance-select",
            "data-allow-clear": "true",
            "data-placeholder": "Select customer",
        })
    )
    date = forms.DateField(
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"})
    )
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01", "placeholder": "0.00"})
    )
    reason = forms.CharField(
        label="Reason / Description",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Opening balance / Adjustment / Fee"})
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional note"})
    )

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields['customer'].queryset = Customer.objects.filter(tenant=tenant).order_by('name')


class CustomerStatementFilterForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        required=False,
        widget=forms.Select(attrs={
            "class": "form-select js-enhance-select",
            "data-allow-clear": "true",
            "data-placeholder": "All customers",
        })
    )
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}))
    end   = forms.DateField(required=False, widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}))

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields['customer'].queryset = Customer.objects.filter(tenant=tenant).order_by('name')
