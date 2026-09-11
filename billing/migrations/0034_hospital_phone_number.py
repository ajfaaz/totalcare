from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0033_customuser_profile_picture_patient_photo'),
    ]

    operations = [
        migrations.AddField(
            model_name='hospital',
            name='phone_number',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
