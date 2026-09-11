from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0032_create_subscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='profile_picture',
            field=models.ImageField(blank=True, null=True, upload_to='user_profiles/'),
        ),
        migrations.AddField(
            model_name='patient',
            name='photo',
            field=models.ImageField(blank=True, null=True, upload_to='patient_photos/'),
        ),
    ]
