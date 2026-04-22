# Generated manually for owner_email field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0030_bill_approved_by_bill_bill_type_bill_finalized_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='hospital',
            name='owner_email',
            field=models.EmailField(default='admin@example.com'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='hospital',
            name='slug',
            field=models.SlugField(blank=True, unique=True),
        ),
    ]