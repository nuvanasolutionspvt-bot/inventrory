from django.db import migrations


def move_tandoori_chicken(apps, schema_editor):
    Tenant = apps.get_model('posapp', 'Tenant')
    Category = apps.get_model('posapp', 'Category')
    Product = apps.get_model('posapp', 'Product')

    for tenant in Tenant.objects.filter(business_type='restaurant'):
        category, _ = Category.objects.get_or_create(tenant=tenant, name='Non-Veg Starters')
        Product.objects.filter(tenant=tenant, name='Tandoori Chicken').update(category=category)


class Migration(migrations.Migration):
    dependencies = [('posapp', '0022_sitesetting_payment_qr')]
    operations = [migrations.RunPython(move_tandoori_chicken, migrations.RunPython.noop)]
