from django.core.exceptions import PermissionDenied

from .models import SiteSetting, Tenant, TenantMembership, TenantFeature


SESSION_TENANT_KEY = 'active_tenant_id'


def tenant_queryset_for_user(user):
    if not user.is_authenticated:
        return Tenant.objects.none()
    return (
        Tenant.objects
        .filter(memberships__user=user, memberships__is_active=True, is_active=True)
        .distinct()
        .order_by('name', 'id')
    )


def get_active_tenant(request):
    tenants = tenant_queryset_for_user(request.user)
    tenant = None
    session_tenant_id = request.session.get(SESSION_TENANT_KEY)

    if session_tenant_id:
        tenant = tenants.filter(pk=session_tenant_id).first()

    if tenant is None:
        tenant = tenants.first()
        if tenant is not None:
            request.session[SESSION_TENANT_KEY] = tenant.pk

    if tenant is None and request.user.is_superuser:
        tenant, _ = Tenant.objects.get_or_create(
            slug='default-store',
            defaults={
                'name': 'Default Store',
                'owner_name': request.user.get_full_name() or request.user.username,
                'contact_email': request.user.email or 'admin@example.com',
                'contact_phone': '',
                'address': '',
                'city': '',
                'state': '',
                'postal_code': '',
            },
        )
        TenantMembership.objects.get_or_create(
            tenant=tenant,
            user=request.user,
            defaults={'role': 'owner'},
        )
        SiteSetting.objects.get_or_create(
            tenant=tenant,
            defaults={'singleton_id': tenant.pk},
        )
        request.session[SESSION_TENANT_KEY] = tenant.pk

    return tenant


def require_active_tenant(request):
    tenant = getattr(request, 'tenant', None) or get_active_tenant(request)
    if tenant is None:
        raise PermissionDenied("No active tenant is assigned to this user.")
    return tenant


def restaurant_inventory_enabled(tenant):
    return bool(tenant and tenant.business_type == 'restaurant'
                and TenantFeature.objects.filter(tenant=tenant, inventory=True).exists())


def can_manage_restaurant_inventory(user, tenant):
    return bool(user and user.is_authenticated and restaurant_inventory_enabled(tenant)
                and user.has_perm('posapp.change_product'))


def restaurant_module_required(module):
    """Enforce saved restaurant module selections without changing other businesses."""
    from functools import wraps

    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            tenant = require_active_tenant(request)
            if tenant.business_type == 'restaurant':
                features, _ = TenantFeature.objects.get_or_create(tenant=tenant)
                if not getattr(features, module, False):
                    raise PermissionDenied('This module is not enabled for this tenant.')
            return view(request, *args, **kwargs)
        return wrapped
    return decorate
