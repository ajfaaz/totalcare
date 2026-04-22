# Adds Hospital.created_at for older databases (production sqlite) that predate the field.

from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0033_hospital_is_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="hospital",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
    ]

