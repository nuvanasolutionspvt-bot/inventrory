from django.core.exceptions import PermissionDenied

from .models import SiteSetting, Tenant, TenantMembership


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
