from django.db import migrations


def rename_category(apps, schema_editor):
    Tenant = apps.get_model('posapp', 'Tenant')
    Category = apps.get_model('posapp', 'Category')
    Product = apps.get_model('posapp', 'Product')

    for tenant in Tenant.objects.filter(business_type='restaurant'):
        old_categories = Category.objects.filter(tenant=tenant, name='Biryani & Rice')
        if not old_categories.exists():
            continue
        new_category, _ = Category.objects.get_or_create(
            tenant=tenant,
            name='Thari Biryani Rice',
        )
        Product.objects.filter(tenant=tenant, category__in=old_categories).update(
            category=new_category,
        )
        old_categories.delete()


def restore_category(apps, schema_editor):
    Tenant = apps.get_model('posapp', 'Tenant')
    Category = apps.get_model('posapp', 'Category')
    Product = apps.get_model('posapp', 'Product')

    for tenant in Tenant.objects.filter(business_type='restaurant'):
        new_categories = Category.objects.filter(tenant=tenant, name='Thari Biryani Rice')
        if not new_categories.exists():
            continue
        old_category, _ = Category.objects.get_or_create(
            tenant=tenant,
            name='Biryani & Rice',
        )
        Product.objects.filter(tenant=tenant, category__in=new_categories).update(
            category=old_category,
        )
        new_categories.delete()


class Migration(migrations.Migration):
    dependencies = [('posapp', '0025_product_portion_prices')]
    operations = [migrations.RunPython(rename_category, restore_category)]
