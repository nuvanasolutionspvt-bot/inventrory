from django.db import migrations, models
import django.db.models.deletion


def create_default_features(apps, schema_editor):
    Tenant = apps.get_model('posapp', 'Tenant')
    TenantFeature = apps.get_model('posapp', 'TenantFeature')
    for tenant in Tenant.objects.all():
        defaults = {
            'pos_billing': True,
            'products_catalog': True,
            'kot_management': False,
            'table_management': False,
            'kitchen_display': False,
            'online_payment': False,
            'inventory': False,
        }
        TenantFeature.objects.get_or_create(tenant=tenant, defaults=defaults)


class Migration(migrations.Migration):

    dependencies = [
        ('posapp', '0028_sitesetting_restaurant_menu_tax_percent'),
    ]

    operations = [
        migrations.CreateModel(
            name='TenantFeature',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pos_billing', models.BooleanField(default=True)),
                ('products_catalog', models.BooleanField(default=True)),
                ('kot_management', models.BooleanField(default=False)),
                ('table_management', models.BooleanField(default=False)),
                ('kitchen_display', models.BooleanField(default=False)),
                ('online_payment', models.BooleanField(default=False)),
                ('inventory', models.BooleanField(default=False)),
                ('tenant', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='features', to='posapp.tenant')),
            ],
            options={
                'verbose_name': 'Tenant feature',
                'verbose_name_plural': 'Tenant features',
            },
        ),
        migrations.RunPython(create_default_features, migrations.RunPython.noop),
    ]
