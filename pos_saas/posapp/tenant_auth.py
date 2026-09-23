from django.contrib.auth.views import LoginView
from .forms import TenantAuthenticationForm
from .tenancy import SESSION_TENANT_KEY


class TenantLoginView(LoginView):
    template_name = 'auth/login.html'
    authentication_form = TenantAuthenticationForm

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.login_tenant_id is not None:
            self.request.session[SESSION_TENANT_KEY] = form.login_tenant_id
        return response
