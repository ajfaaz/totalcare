from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import UserCreationForm, SetPasswordForm
from django.contrib.auth import get_user_model
from django.forms.widgets import DateInput, TimeInput, Textarea, Select
from .models import (
    Patient,
    PatientCoverage,
    Payer,
    Appointment,
    Bill,
    BillItem,
    Payment,
    MedicalRecord,
    LabReport,
    RadiologyReport,
    CustomUser,
    Prescription,
)

# ----------------- PATIENT -----------------
class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ['full_name', 'date_of_birth', 'phone_number', 'address']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
        }


class PatientRegistrationForm(forms.ModelForm):
    payer = forms.ModelChoiceField(
        queryset=Payer.objects.all(),
        required=True,
        label="Payment Scheme"
    )

    class Meta:
        model = Patient
        fields = [
            "full_name",
            "date_of_birth",
            "phone_number",
            "address",
        ]

    def save(self, commit=True):
        patient = super().save(commit=commit)

        # Only create coverage when the patient has been saved (commit=True).
        if commit:
            payer = self.cleaned_data.get('payer')
            if payer:
                PatientCoverage.objects.update_or_create(
                    patient=patient,
                    defaults={
                        'payer': payer,
                        'patient_percentage': 0,
                        'government_percentage': 100,
                        'active': True,
                    }
                )

        return patient


# ----------------- USER -----------------
User = get_user_model()

class CustomUserCreationForm(UserCreationForm):
    first_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First name"})
    )
    last_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name"})
    )
    email = forms.EmailField(required=True)
    role = forms.ChoiceField(
        choices=User.USER_ROLE_CHOICES,
        required=True,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "username",
            "email",
            "role",
            "specialty",
            "password1",
            "password2",
        )

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("request_user", None)
        super().__init__(*args, **kwargs)
        self.fields["role"].help_text = "Choose the staff member's access level."
        self.fields["specialty"].help_text = "Optional. Use for doctors or other specialist roles."

        # Prevent hospital admins from creating platform admins (UI-level).
        if self.request_user is not None and getattr(self.request_user, "role", None) != "platform_admin":
            self.fields["role"].choices = [
                (value, label)
                for (value, label) in self.fields["role"].choices
                if value != "platform_admin"
            ]

        for field_name in ("username", "email", "specialty", "password1", "password2"):
            self.fields[field_name].widget.attrs.update({"class": "form-control"})
        self.fields["username"].widget.attrs.setdefault("placeholder", "Username")
        self.fields["email"].widget.attrs.setdefault("placeholder", "Email address")
        self.fields["specialty"].widget.attrs.setdefault("placeholder", "e.g. Cardiology")
        self.fields["password1"].widget.attrs.setdefault("placeholder", "Password")
        self.fields["password2"].widget.attrs.setdefault("placeholder", "Confirm password")

    def save(self, commit=True, hospital=None):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = self.cleaned_data["email"]
        user.role = self.cleaned_data["role"]
        if not hospital:
            raise ValueError("A hospital must be provided when creating a user.")
        user.hospital = hospital
        if commit:
            user.save()
        return user

    def clean_role(self):
        role = self.cleaned_data.get("role")
        # Server-side protection (handles request tampering).
        if self.request_user is not None and getattr(self.request_user, "role", None) != "platform_admin":
            if role == "platform_admin":
                raise ValidationError("You are not allowed to create a platform admin user.")
        return role


class StaffUserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "username", "email", "role", "specialty", "is_active")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name"}),
            "username": forms.TextInput(attrs={"class": "form-control", "placeholder": "Username"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email address"}),
            "role": forms.Select(attrs={"class": "form-control"}),
            "specialty": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Cardiology"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("request_user", None)
        super().__init__(*args, **kwargs)
        self.fields["role"].help_text = "Update the staff member's access level."
        self.fields["specialty"].help_text = "Optional. Useful for doctors and specialist roles."

        # Prevent hospital admins from promoting users to platform admin (UI-level).
        if self.request_user is not None and getattr(self.request_user, "role", None) != "platform_admin":
            self.fields["role"].choices = [
                (value, label)
                for (value, label) in self.fields["role"].choices
                if value != "platform_admin"
            ]

    def clean_role(self):
        role = self.cleaned_data.get("role")
        # Server-side protection (handles request tampering).
        if self.request_user is not None and getattr(self.request_user, "role", None) != "platform_admin":
            if role == "platform_admin":
                raise ValidationError("You are not allowed to assign the platform admin role.")
        return role


class StaffPasswordResetForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        for field_name in ("new_password1", "new_password2"):
            self.fields[field_name].widget.attrs.update({"class": "form-control"})
        self.fields["new_password1"].widget.attrs.setdefault("placeholder", "New password")
        self.fields["new_password2"].widget.attrs.setdefault("placeholder", "Confirm new password")


# ----------------- BILLING -----------------
class BillItemForm(forms.ModelForm):
    class Meta:
        model = BillItem
        fields = '__all__'


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = '__all__'


# ----------------- APPOINTMENT -----------------
class AppointmentForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ['patient', 'date', 'time', 'reason']
        widgets = {
            'date': DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'time': TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'),
            'reason': Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'patient': Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].input_formats = ['%Y-%m-%d']
        self.fields['time'].input_formats = ['%H:%M']

    def save(self, commit=True):
        appointment = super().save(commit=False)
        if commit:
            appointment.save()
        return appointment


# ----------------- MEDICAL RECORD -----------------
class MedicalRecordForm(forms.ModelForm):
    class Meta:
        model = MedicalRecord
        fields = ["patient", "diagnosis", "treatment", "notes"]  # ✅ fixed (note not notes)

from django import forms
from .models import LabReport

class LabReportForm(forms.ModelForm):
    class Meta:
        model = LabReport
        fields = ["test_name", "result"]   


class RadiologyReportForm(forms.ModelForm):
    patient_name = forms.CharField(
        label="Patient",
        required=False,
        disabled=True,   # ✅ readonly
    )

    class Meta:
        model = RadiologyReport
        fields = ["patient_name", "scan_type", "report"]  # ✅ show patient but not editable

    def __init__(self, *args, **kwargs):
        patient = kwargs.pop("patient", None)
        super().__init__(*args, **kwargs)
        if patient:
            self.fields["patient_name"].initial = patient.full_name



# ----------------- Prescription -----------------
from django import forms
from .models import Prescription

class PrescriptionForm(forms.ModelForm):
    reason = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Reason for visit (optional)"}),
        help_text="Specify the reason for this visit if creating a new visit"
    )
    class Meta:
        model = Prescription
        fields = ["medicines", "dosage", "duration", "instructions"]
        widgets = {
            "medicines": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 5,
                    "placeholder": "Enter one medicine per line, e.g.\nParacetamol 500mg - twice daily\nIbuprofen 200mg - after meals"
                }
            ),
            "dosage": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., 1 tablet"}),
            "duration": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., 5 days"}),
            "instructions": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Additional notes for the patient"
                }
            ),
        }

# ----------------- Hospital SLA Form -----------------

from .models import Hospital, SLAPolicy, Service

class HospitalSLAForm(forms.ModelForm):
    class Meta:
        model = Hospital
        fields = [
            "sla_doctor_ack_minutes",
            "sla_head_doctor_minutes",
            "sla_admin_minutes",
        ]

class HospitalCreateForm(forms.ModelForm):
    class Meta:
        model = Hospital
        fields = [
            "name",
            "owner_email",
            "logo",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Hospital name"}),
            "owner_email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "Owner email"}),
            "logo": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }


class SLAPolicyForm(forms.ModelForm):
    class Meta:
        model = SLAPolicy
        fields = [
            "severity",
            "response_time_minutes",
            "escalation_time_minutes",
            "max_escalation_level",
            "active",
        ]


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ["name", "description", "price"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Consultation"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional"}),
            "price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Service name is required.")
        return name
