from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0031_manual_hospital_owner_email'),
    ]

    operations = [
        migrations.CreateModel(
            name='Subscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan', models.CharField(choices=[('basic', 'Basic'), ('standard', 'Standard'), ('premium', 'Premium')], max_length=20)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('is_active', models.BooleanField(default=True)),
                ('hospital', models.OneToOneField(on_delete=models.CASCADE, to='billing.hospital')),
            ],
        ),
    ]
