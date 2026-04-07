from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.text import slugify
from django.contrib.auth import get_user_model

from .models import Hospital
from billing.models import VitalSign
from messaging.models import Message
from billing.utils.vitals import evaluate_vitals

User = get_user_model()


# ---------------------------------------------------
# Assign default hospital to new users
# ---------------------------------------------------
@receiver(post_save, sender=User)
def assign_default_hospital(sender, instance, created, **kwargs):
    if created and instance.hospital is None:
        hospital, _ = Hospital.objects.get_or_create(
            name="Default Hospital",
            defaults={"slug": slugify("Default Hospital")},
        )

        # Prevent recursion
        User.objects.filter(id=instance.id).update(hospital=hospital)


# ---------------------------------------------------
# Notify doctors when vitals are critical
# ---------------------------------------------------
@receiver(post_save, sender=VitalSign)
def notify_doctor_on_critical_vitals(sender, instance, created, **kwargs):

    if not created:
        return

    alerts = evaluate_vitals(instance)

    if "critical" not in alerts.values():
        return

    patient = instance.patient
    hospital = patient.hospital

    if not hospital:
        return

    recipients = []

    # Notify assigned doctor first
    if instance.visit and instance.visit.assigned_doctor:
        doctor = instance.visit.assigned_doctor

        if doctor.hospital == hospital:
            recipients.append(doctor)

    # Otherwise notify all doctors in same hospital
    else:
        recipients = User.objects.filter(
            hospital=hospital,
            role="doctor",
            is_active=True
        )

    if not recipients:
        return

    critical_items = [
        name.replace("_", " ").title()
        for name, level in alerts.items()
        if level == "critical"
    ]

    for doctor in recipients:
        Message.objects.create(
            hospital=hospital,  # 🔒 critical isolation
            sender=instance.recorded_by,
            recipient=doctor,
            subject="🚨 CRITICAL VITALS ALERT",
            body=(
                f"Patient: {patient.full_name}\n"
                f"Critical Vitals: {', '.join(critical_items)}\n"
                f"Recorded at: {instance.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                f"View EMR: /patients/{patient.id}/emr/"
            ),
        )