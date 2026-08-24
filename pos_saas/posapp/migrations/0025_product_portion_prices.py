from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('posapp', '0024_split_restaurant_main_course_categories')]
    operations = [
        migrations.AddField(model_name='product', name='full_available', field=models.BooleanField(default=True)),
        migrations.AddField(model_name='product', name='half_price', field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
    ]
