import django.db.models.deletion
from django.db import migrations, models


TENANT_OWNED_MODELS = [
    "Category",
    "Product",
    "ProductSet",
    "Supplier",
    "Customer",
    "Purchase",
    "Sale",
    "StockMove",
    "CustomerLedger",
]


def assign_default_tenant(apps, schema_editor):
    Tenant = apps.get_model("posapp", "Tenant")
    SiteSetting = apps.get_model("posapp", "SiteSetting")

    tenant, _ = Tenant.objects.get_or_create(
        slug="default-store",
        defaults={
            "name": "Default Store",
            "business_type": "retail_store",
            "owner_name": "Admin",
            "contact_email": "admin@example.com",
            "contact_phone": "",
            "address": "",
            "city": "",
            "state": "",
            "postal_code": "",
            "country": "India",
            "plan": "starter",
            "is_active": True,
        },
    )

    for model_name in TENANT_OWNED_MODELS:
        Model = apps.get_model("posapp", model_name)
        Model.objects.filter(tenant__isnull=True).update(tenant=tenant)

    SiteSetting.objects.filter(tenant__isnull=True).update(tenant=tenant)


class Migration(migrations.Migration):

    dependencies = [
        ("posapp", "0012_tenant_business_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="category",
            name="name",
            field=models.CharField(max_length=120),
        ),
        migrations.AlterField(
            model_name="product",
            name="code",
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name="product",
            name="barcode",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name="productset",
            name="code",
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="singleton_id",
            field=models.PositiveIntegerField(default=1, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterModelOptions(
            name="category",
            options={"ordering": ["name"]},
        ),
        migrations.AlterModelOptions(
            name="productset",
            options={"ordering": ["code"]},
        ),
        migrations.AddField(
            model_name="category",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="categories", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="product",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="products", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="productset",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="product_sets", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="supplier",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="suppliers", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="customer",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="customers", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="purchase",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="purchases", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="sale",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="sales", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="stockmove",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="stock_moves", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="customerledger",
            name="tenant",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="customer_ledger_lines", to="posapp.tenant"),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="tenant",
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="site_setting", to="posapp.tenant"),
        ),
        migrations.RunPython(assign_default_tenant, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="category",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="categories", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="product",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="products", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="productset",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="product_sets", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="supplier",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="suppliers", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="customer",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customers", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="purchase",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="purchases", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="sale",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sales", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="stockmove",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stock_moves", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="customerledger",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customer_ledger_lines", to="posapp.tenant"),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="tenant",
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="site_setting", to="posapp.tenant"),
        ),
        migrations.AddConstraint(
            model_name="category",
            constraint=models.UniqueConstraint(fields=("tenant", "name"), name="uniq_tenant_category_name"),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(fields=("tenant", "code"), name="uniq_tenant_product_code"),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                condition=models.Q(barcode__isnull=False) & ~models.Q(barcode=""),
                fields=("tenant", "barcode"),
                name="uniq_tenant_product_barcode",
            ),
        ),
        migrations.AddConstraint(
            model_name="productset",
            constraint=models.UniqueConstraint(fields=("tenant", "code"), name="uniq_tenant_product_set_code"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["tenant", "is_active"], name="posapp_prod_tenant__12c615_idx"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["tenant", "name"], name="posapp_prod_tenant__e1db27_idx"),
        ),
        migrations.AddIndex(
            model_name="supplier",
            index=models.Index(fields=["tenant", "name"], name="posapp_supp_tenant__0730e0_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["tenant", "name"], name="posapp_cust_tenant__5dd38d_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["tenant", "phone"], name="posapp_cust_tenant__9f14e4_idx"),
        ),
        migrations.AddIndex(
            model_name="purchase",
            index=models.Index(fields=["tenant", "date"], name="posapp_purc_tenant__798ec0_idx"),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["tenant", "date"], name="posapp_sale_tenant__88f376_idx"),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["tenant", "is_return"], name="posapp_sale_tenant__960f03_idx"),
        ),
        migrations.AddIndex(
            model_name="stockmove",
            index=models.Index(fields=["tenant", "reason"], name="posapp_stoc_tenant__db2e44_idx"),
        ),
        migrations.AddIndex(
            model_name="stockmove",
            index=models.Index(fields=["tenant", "ref"], name="posapp_stoc_tenant__7b3064_idx"),
        ),
        migrations.AddIndex(
            model_name="customerledger",
            index=models.Index(fields=["tenant", "date"], name="posapp_cust_tenant__286454_idx"),
        ),
    ]
