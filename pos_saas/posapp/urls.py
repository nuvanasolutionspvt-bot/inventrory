from .contact import contact_us
from django.views.generic import TemplateView
from django.urls import path
from . import views, payment_views

urlpatterns = [
    path('restaurant/payments/<int:sale_id>/', payment_views.payment_checkout, name='payment_checkout'),
    path('restaurant/payments/<int:sale_id>/order/', payment_views.razorpay_order, name='razorpay_order'),
    path('restaurant/payments/<int:sale_id>/verify/', payment_views.razorpay_verify, name='razorpay_verify'),
    path('restaurant/payments/<int:sale_id>/check/', payment_views.razorpay_check, name='razorpay_check'),
    path('contact-us/', contact_us, name='contact_us'),
    path('restaurant/waiter/notifications/', views.waiter_order_notifications, name='waiter_order_notifications'),
    path('', TemplateView.as_view(template_name='home.html'), name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('register/', views.register, name='register'),
    path('restaurant/catalog/setup/', views.restaurant_catalog_setup, name='restaurant_catalog_setup'),
    path('restaurant/modules/inventory/ingredients/new/', views.ingredient_edit, name='ingredient_create'),
    path('restaurant/modules/inventory/ingredients/<int:pk>/edit/', views.ingredient_edit, name='ingredient_edit'),
    path('restaurant/modules/inventory/purchases/new/', views.ingredient_purchase, name='ingredient_purchase'),
    path('restaurant/modules/<slug:module>/', views.restaurant_module_page, name='restaurant_module_page'),
    path('restaurant/tables/', views.restaurant_tables, name='restaurant_tables'),
    path('restaurant/tables/<int:table_id>/delete/', views.restaurant_table_delete, name='restaurant_table_delete'),
    path('restaurant/kitchen/orders/', views.kitchen_orders, name='kitchen_orders'),
    path('restaurant/kitchen/orders/<int:kot_id>/status/', views.kitchen_order_status, name='kitchen_order_status'),
    path('restaurant/kitchen/display/', views.kitchen_display, name='kitchen_display'),
    path('company/', views.company_dashboard, name='company_dashboard'),
    path('company/businesses/', views.company_business_list, name='company_business_list'),
    path('company/businesses/new/', views.company_business_create, name='company_business_create'),
    path('company/businesses/<int:tenant_id>/edit/', views.company_business_edit, name='company_business_edit'),
    path('company/businesses/<int:tenant_id>/delete/', views.company_business_delete, name='company_business_delete'),
    path('subscription/', views.subscription, name='subscription'),
    path('subscription/trial/', views.subscription_trial, name='subscription_trial'),
    path('subscription/checkout/<str:plan_code>/', views.subscription_checkout, name='subscription_checkout'),
    path('subscription/verify/', views.subscription_verify, name='subscription_verify'),

    # Products & Masters
    path('products/', views.product_list, name='product_list'),
    path('products/new/', views.product_create, name='product_create'),
    path('products/<int:pk>/edit/', views.product_update, name='product_update'),
    path('sets/', views.product_set_list, name='product_set_list'),
    path('sets/new/', views.product_set_create, name='product_set_create'),
    path('sets/<int:pk>/edit/', views.product_set_update, name='product_set_update'),
    path('products/<int:pk>/add_stock/', views.product_add_stock, name='product_add_stock'),
    path('stock/add/', views.product_add_stock, name='stock_add'),
    path('stock/bulk/', views.stock_bulk_adjust, name='stock_bulk_adjust'),
    path('stock/bulk/template/', views.stock_bulk_template, name='stock_bulk_template'),
    path('products/export/', views.product_export, name='product_export'),
    path('products/import/', views.product_import, name='product_import'),
    path('products/barcodes/', views.barcode_labels, name='product_barcodes'),

    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/new/', views.supplier_create, name='supplier_create'),

    path('customers/', views.customer_list, name='customer_list'),
    path('customers/new/', views.customer_create, name='customer_create'),
    path('quick/new/', views.customer_quick_new, name='customer_quick_new'),          # GET -> partial form HTML
    path('quick/create/', views.customer_quick_create, name='customer_quick_create'), # POST -> JSON

    # Purchases
    path('purchases/new/', views.purchase_create, name='purchase_create'),
    path('purchases/<int:purchase_id>/edit/', views.purchase_update, name='purchase_update'),

    # Sales (POS)
    path('pos/', views.pos_sale_create, name='pos_sale_create'),
    path('invoice/<int:sale_id>/', views.invoice_view, name='invoice_view'),

    # Reports
    path('reports/sales/', views.sales_report, name='sales_report'),
    path('reports/stock/', views.stock_report, name='stock_report'),
    path('reports/batch-stock/', views.batch_stock_report, name='batch_stock_report'),
    path('reports/purchases/', views.purchase_report, name='purchase_report'),

    path('stock/bulk_adjust/', views.stock_bulk_adjust, name='stock_bulk_adjust'),

    path('sales/', views.sales_list, name='sales_list'),
    path('sales/<int:sale_id>/edit/', views.sale_update, name='sale_update'),
    # ADD to your urlpatterns
    path('security/users/', views.security_users, name='security_users'),
    path('security/users/new/', views.security_user_new, name='security_user_new'),
    path('security/users/<int:user_id>/delete/', views.security_user_delete, name='security_user_delete'),
    path('security/users/<int:user_id>/edit/', views.security_user_edit, name='security_user_edit'),

    path('security/roles/', views.security_roles, name='security_roles'),
    path('security/roles/new/', views.security_role_new, name='security_role_new'),
    path('security/roles/<int:role_id>/edit/', views.security_role_edit, name='security_role_edit'),
    path('settings/', views.settings_general, name='settings_general'),

    path('credit/receive/', views.receive_payment, name='receive_payment'),
    path('credit/receipt/<int:ledger_id>/', views.payment_receipt, name='payment_receipt'),
    path('credit/charge/', views.customer_charge, name='customer_charge'),
    path('credit/statement/', views.customer_statement, name='customer_statement'),
    path('settings/', views.settings_general, name='settings_general'),
    path('api/customer/<int:customer_id>/balance/', views.customer_balance_api, name='customer_balance_api'),

    path('settings/backup/now/', views.backup_download_now, name='backup_download_now'),
    path('settings/backup/restore/', views.backup_restore_upload, name='backup_restore_upload'),

]

