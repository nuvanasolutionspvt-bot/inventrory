from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import Permission

from .models import TenantMembership


class TenantAdminBackend(BaseBackend):
    """Grant application permissions to admins of the current store."""

    def get_all_permissions(self, user_obj, obj=None):
        tenant = getattr(user_obj, '_active_permission_tenant', None)
        if obj is not None or not user_obj.is_active or tenant is None or not tenant.is_active:
            return set()
        membership = TenantMembership.objects.filter(user=user_obj, tenant=tenant, is_active=True).first()
        if membership is None:
            return set()
        if membership.role not in {'owner', 'admin'} and not user_obj.groups.filter(name__in=['Admin', 'Restaurant Admin']).exists():
            return set()
        return {'posapp.' + codename for codename in Permission.objects.filter(
            content_type__app_label='posapp',
        ).values_list('codename', flat=True)}
