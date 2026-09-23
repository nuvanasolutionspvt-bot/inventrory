from django import template
from posapp.models import TenantMembership

register = template.Library()


@register.filter
def tenant_username(user, tenant):
    if not user:
        return ''
    name = TenantMembership.objects.filter(tenant=tenant, user=user).values_list('login_username', flat=True).first()
    return name or user.username
