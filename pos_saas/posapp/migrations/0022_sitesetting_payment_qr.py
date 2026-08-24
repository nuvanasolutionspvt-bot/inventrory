from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('posapp', '0021_split_restaurant_starter_categories')]
    operations = [
        migrations.AddField(
            model_name='sitesetting',
            name='payment_qr',
            field=models.ImageField(blank=True, null=True, upload_to='tenant_qr/%Y/%m/'),
        ),
    ]
