from django.db import migrations


def split_main_course(apps, schema_editor):
    Tenant = apps.get_model('posapp', 'Tenant')
    Category = apps.get_model('posapp', 'Category')
    Product = apps.get_model('posapp', 'Product')

    veg_names = ('Dal Tadka', 'Paneer Butter Masala')
    non_veg_names = ('Butter Chicken', 'Chicken Curry')
    for tenant in Tenant.objects.filter(business_type='restaurant'):
        veg_category, _ = Category.objects.get_or_create(tenant=tenant, name='Veg Main Course')
        non_veg_category, _ = Category.objects.get_or_create(tenant=tenant, name='Non-Veg Main Course')
        Product.objects.filter(tenant=tenant, name__in=veg_names).update(category=veg_category)
        Product.objects.filter(tenant=tenant, name__in=non_veg_names).update(category=non_veg_category)
        Category.objects.filter(tenant=tenant, name='Main Course', product__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('posapp', '0023_move_tandoori_chicken_to_starters')]
    operations = [migrations.RunPython(split_main_course, migrations.RunPython.noop)]
