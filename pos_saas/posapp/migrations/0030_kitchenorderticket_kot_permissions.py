from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('posapp', '0029_tenantfeature'),
    ]

    operations = [
        migrations.CreateModel(
            name='KitchenOrderTicket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ticket_no', models.CharField(max_length=32)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('preparing', 'Preparing'), ('ready', 'Ready')], default='pending', max_length=20)),
                ('notes', models.TextField(blank=True, default='')),
                ('sale', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='kot', to='posapp.sale')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='kitchen_orders', to='posapp.tenant')),
            ],
            options={
                'ordering': ['-created_at'],
                'permissions': [('can_manage_kot', 'Can manage kitchen orders'), ('can_view_kds', 'Can view kitchen display')],
            },
        ),
        migrations.AddConstraint(
            model_name='kitchenorderticket',
            constraint=models.UniqueConstraint(fields=('tenant', 'ticket_no'), name='uniq_tenant_kot_ticket_no'),
        ),
        migrations.AddIndex(
            model_name='kitchenorderticket',
            index=models.Index(fields=['tenant', 'status'], name='posapp_kot_tenant_status_idx'),
        ),
        migrations.AddIndex(
            model_name='kitchenorderticket',
            index=models.Index(fields=['tenant', 'created_at'], name='posapp_kot_tenant_created_idx'),
        ),
        migrations.AlterModelOptions(
            name='apppermission',
            options={'default_permissions': (), 'managed': False, 'permissions': [('can_pos', 'Can use POS (sell/return)'), ('can_view_reports', 'Can view and download reports'), ('can_print_barcodes', 'Can print barcode labels'), ('can_adjust_stock', 'Can adjust stock (quick/bulk)'), ('can_manage_purchases', 'Can create purchases'), ('can_manage_settings', 'Can manage POS settings'), ('can_manage_users', 'Can manage users and roles'), ('can_credit_receive', 'Can receive customer payments'), ('can_credit_charge', 'Can post customer charges/fees'), ('can_credit_view', 'Can view customer credit statements'), ('can_manage_kot', 'Can manage kitchen orders'), ('can_view_kds', 'Can view kitchen display')]},
        ),
    ]
