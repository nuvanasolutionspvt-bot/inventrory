from django.db import migrations


def initialize_tenant_settings(apps, schema_editor):
    Tenant = apps.get_model("posapp", "Tenant")
    SiteSetting = apps.get_model("posapp", "SiteSetting")

    for tenant in Tenant.objects.all():
        setting, _ = SiteSetting.objects.get_or_create(
            tenant=tenant,
            defaults={
                "singleton_id": tenant.pk,
                "org_name": tenant.name,
                "org_address": tenant.address,
                "org_phone": tenant.contact_phone,
                "org_email": tenant.contact_email,
            },
        )

        changed = False
        if setting.org_name in ("", "Your Store"):
            setting.org_name = tenant.name
            changed = True
        if not setting.org_address and tenant.address:
            setting.org_address = tenant.address
            changed = True
        if not setting.org_phone and tenant.contact_phone:
            setting.org_phone = tenant.contact_phone
            changed = True
        if not setting.org_email and tenant.contact_email:
            setting.org_email = tenant.contact_email
            changed = True
        if changed:
            setting.save(update_fields=["org_name", "org_address", "org_phone", "org_email"])


class Migration(migrations.Migration):

    dependencies = [
        ("posapp", "0013_tenant_scope_business_data"),
    ]

    operations = [
        migrations.RunPython(initialize_tenant_settings, migrations.RunPython.noop),
    ]
