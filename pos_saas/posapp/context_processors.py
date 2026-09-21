from .models import TenantFeature
from .payments import checkout_available
from .tenancy import can_manage_restaurant_inventory


def kitchen_notifications_admin(user, tenant):
    return bool(user and user.is_authenticated and (
        user.is_superuser
        or user.has_perm('posapp.can_manage_users')
        or user.groups.filter(name__in=['Admin', 'Restaurant Admin']).exists()
        or user.tenant_memberships.filter(tenant=tenant, is_active=True, role__in=['owner', 'admin']).exists()
    ))


def tenant_features(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return {'tenant_features': None}
    features, _created = TenantFeature.objects.get_or_create(
        tenant=tenant,
        defaults={
            'pos_billing': True,
            'products_catalog': True,
        },
    )
    user = getattr(request, 'user', None)
    waiter_notifications = bool(
        tenant.business_type == 'restaurant' and features.kot_management
        and user and user.is_authenticated
        and user.has_perm('posapp.can_pos')
        and (kitchen_notifications_admin(user, tenant) or user.groups.filter(name='Waiter').exists())
    )
    return {'tenant_features': features, 'waiter_notifications': waiter_notifications,
            'restaurant_inventory_access': can_manage_restaurant_inventory(user, tenant),
            'razorpay_available': checkout_available(tenant)}
