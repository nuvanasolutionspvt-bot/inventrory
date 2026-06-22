from .tenancy import get_active_tenant


class ActiveTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        if getattr(request, 'user', None) is not None and request.user.is_authenticated:
            request.tenant = get_active_tenant(request)
        return self.get_response(request)
