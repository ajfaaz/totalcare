from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from faker import Faker
import random
from datetime import date, timedelta

from billing.models import Hospital, Patient, Prescription, PatientVisit

User = get_user_model()


class Command(BaseCommand):
    help = "Seed demo hospital data"

    def handle(self, *args, **kwargs):
        fake = Faker()

        self.stdout.write("Creating hospital...")

        hospital_name = "TotalCare Demo Hospital"
        hospital_slug = slugify(hospital_name)

        hospital, created = Hospital.objects.get_or_create(
            slug=hospital_slug,
            defaults={
                "name": hospital_name,
            }
        )

        # -------------------------
        # USERS
        # -------------------------

        self.stdout.write("Creating demo users...")

        admin, created = User.objects.get_or_create(
            username="admin1",
            defaults={
                "role": "admin",
                "hospital": hospital
            }
        )
        if created:
            admin.set_password("admin123")
            admin.save()

        doctor, created = User.objects.get_or_create(
            username="doctor1",
            defaults={
                "role": "doctor",
                "hospital": hospital,
                "specialty": "General Medicine"
            }
        )
        if created:
            doctor.set_password("doctor123")
            doctor.save()

        receptionist, created = User.objects.get_or_create(
            username="reception1",
            defaults={
                "role": "receptionist",
                "hospital": hospital
            }
        )
        if created:
            receptionist.set_password("reception123")
            receptionist.save()

        pharmacist, created = User.objects.get_or_create(
            username="pharma1",
            defaults={
                "role": "pharmacist",
                "hospital": hospital
            }
        )
        if created:
            pharmacist.set_password("pharma123")
            pharmacist.save()

        # Add nurse user
        nurse, created = User.objects.get_or_create(
            username="nurse1",
            defaults={
                "role": "nurse",
                "hospital": hospital
            }
        )
        if created:
            nurse.set_password("nurse123")
            nurse.save()

        # -------------------------
        # PATIENTS + VISITS + PRESCRIPTIONS
        # -------------------------

        self.stdout.write("Creating demo patients...")

        # In your seed_demo.py command
        for _ in range(20):
            patient = Patient.objects.create(
                hospital=hospital,
                full_name=fake.name(),
                date_of_birth=fake.date_of_birth(minimum_age=18, maximum_age=80),
                phone_number=fake.phone_number()[:20],  # ✅ TRUNCATE TO 20 CHARS
                address=fake.address(),
            )

            visit = PatientVisit.objects.create(
                hospital=hospital,
                patient=patient,
                assigned_doctor=doctor,
            )

            Prescription.objects.create(
                hospital=hospital,
                visit=visit,
                doctor=doctor,
                pharmacist=pharmacist,
                medicines=fake.word(),
                dosage="1 tablet twice daily",
                duration="5 days",
                instructions="Take after meals",
                status=random.choice(["issued", "dispensed"]),
            )

        self.stdout.write(self.style.SUCCESS("Demo data created successfully!"))