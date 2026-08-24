from django.db import migrations


def split_restaurant_starters(apps, schema_editor):
    Tenant = apps.get_model('posapp', 'Tenant')
    Category = apps.get_model('posapp', 'Category')
    Product = apps.get_model('posapp', 'Product')

    veg_names = ('Paneer Tikka', 'Veg Spring Roll')
    non_veg_names = (
        'Chicken Tikka', 'Crispy Chicken', 'Chicken Schezwan',
        'Dragon Chicken', 'Chilli Chicken',
    )
    for tenant in Tenant.objects.filter(business_type='restaurant'):
        veg_category, _ = Category.objects.get_or_create(tenant=tenant, name='Veg Starters')
        non_veg_category, _ = Category.objects.get_or_create(tenant=tenant, name='Non-Veg Starters')
        Product.objects.filter(tenant=tenant, name__in=veg_names).update(category=veg_category)
        Product.objects.filter(tenant=tenant, name__in=non_veg_names).update(category=non_veg_category)
        Category.objects.filter(tenant=tenant, name='Starters', product__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('posapp', '0020_saleitem_product_batch')]
    operations = [migrations.RunPython(split_restaurant_starters, migrations.RunPython.noop)]
