from django.contrib import admin
from django.urls import path, include, re_path
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from posapp.forms import CompanyAuthenticationForm
from django.conf import settings
from django.views.static import serve
import mimetypes

mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('image/avif', '.avif')

urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        'company/login/',
        auth_views.LoginView.as_view(
            template_name='company/login.html',
            authentication_form=CompanyAuthenticationForm,
            next_page=reverse_lazy('company_dashboard'),
        ),
        name='company_login',
    ),
    path(
        'company/logout/',
        auth_views.LogoutView.as_view(next_page=reverse_lazy('company_login')),
        name='company_logout',
    ),
    path('login/', auth_views.LoginView.as_view(template_name='auth/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    path('', include('posapp.urls')),
]
