from io import BytesIO
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q, Max, F, Count, Avg, Value
from django.db.models.functions import TruncMonth, Concat
from django.contrib.auth.forms import AuthenticationForm
from django.http import HttpResponse, HttpResponseForbidden
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.template.loader import get_template, render_to_string
from django.conf import settings
from .models import MedicineCategory
from billing.utils.vitals import evaluate_vitals
import json
from xhtml2pdf import pisa
from datetime import timedelta
from .models import ConsultationNote



from .forms import (
    BillItemForm,
    PaymentForm,
    AppointmentForm,
    CustomUserCreationForm,
    StaffPasswordResetForm,
    StaffUserUpdateForm,
    MedicalRecordForm,
    LabReportForm,
    RadiologyReportForm,
    PatientRegistrationForm,
    HospitalSLAForm,
    HospitalCreateForm,
    ServiceForm,
)
from .models import (
    Appointment,
    Patient,
    Service,
    Bill,
    BillItem,
    Payment,
    AuditLog,
    PatientVisit,
    CustomUser,
    Hospital,
    MedicalRecord,
    LabReport,
    RadiologyReport,
    VitalSign,
    Medicine,
    VitalAlert,
    VitalAlertLog,
    SLAPolicy,
    PatientCoverage,
    ThirdPartyPayer,
    Payer,
    LabTestRequest,
    RadiologyRequest,
    Subscription,
    
)
from billing.utils.sla import sla_remaining_time, sla_timer_state
from billing.utils.sla_metrics import doctor_sla_metrics
from billing.utils.department_sla import department_sla_metrics
from billing.utils.scorecard import performance_grade
from billing.utils.audit import log_action
from billing.utils.billing import calculate_bill_split

from messaging.forms import MessageForm
from messaging.models import Message

User = get_user_model()


def hospital_scoped_or_404(model, user, **filters):
    return get_object_or_404(model, hospital=user.hospital, **filters)


def user_can_view_bill(user, bill):
    if user.role in ["admin", "accountant"]:
        return True
    if user.role == "receptionist" and bill.bill_type == "front_desk":
        return True
    if user.role == "pharmacist" and bill.bill_type == "pharmacy":
        return True
    return False


def user_can_record_payment(user):
    return user.role in ["accountant", "admin"]


def user_can_create_front_desk_bill(user):
    return user.role in ["receptionist", "admin"]


def user_can_create_pharmacy_bill(user):
    return user.role in ["pharmacist", "admin"]


def platform_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.role != "platform_admin":
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return wrapper


def ensure_active_visit_for_appointment(appointment, started_by):
    active_visit = (
        PatientVisit.objects.filter(
            patient=appointment.patient,
            hospital=appointment.hospital,
            is_active=True,
        )
        .exclude(status="completed")
        .first()
    )

    if active_visit:
        updated_fields = []
        if appointment.doctor and active_visit.assigned_doctor_id != appointment.doctor_id:
            active_visit.assigned_doctor = appointment.doctor
            updated_fields.append("assigned_doctor")
        if started_by and active_visit.assigned_by_id != started_by.id:
            active_visit.assigned_by = started_by
            updated_fields.append("assigned_by")
        if active_visit.status not in ["pending", "under_diagnosis", "lab_requested", "radiology_requested", "lab_completed", "radiology_completed", "prescribed"]:
            active_visit.status = "pending"
            updated_fields.append("status")
        if updated_fields:
            active_visit.save(update_fields=updated_fields)
        return active_visit, False

    visit = PatientVisit.objects.create(
        hospital=appointment.hospital,
        patient=appointment.patient,
        assigned_doctor=appointment.doctor,
        assigned_by=started_by,
        status="pending",
        is_active=True,
        is_emergency=appointment.is_walk_in,
        reason=appointment.reason,
    )
    return visit, True


def build_admin_dashboard_context(request, form=None):
    hospital = request.user.hospital
    last_30_days = timezone.now() - timedelta(days=30)
    staff_query = request.GET.get("staff_query", "").strip()
    staff_role = request.GET.get("staff_role", "").strip()

    if form is None:
        form = CustomUserCreationForm()

    income_by_month = (
        Payment.objects.filter(hospital=hospital)
        .annotate(month=TruncMonth("paid_on"))
        .values("month")
        .annotate(total=Sum("amount_paid"))
        .order_by("month")
    )

    total_income = Payment.objects.filter(hospital=hospital).aggregate(
        total=Sum("amount_paid")
    )["total"] or 0
    monthly_income = Payment.objects.filter(
        hospital=hospital,
        paid_on__gte=last_30_days
    ).aggregate(total=Sum("amount_paid"))["total"] or 0

    total_alerts = VitalAlert.objects.filter(patient__hospital=hospital).count()
    critical_open = VitalAlert.objects.filter(
        patient__hospital=hospital,
        status__in=["open", "acknowledged", "escalated"],
        vital__status="critical",
    ).count()
    escalations = VitalAlert.objects.filter(
        patient__hospital=hospital,
        escalated=True
    ).count()
    resolved_alerts = VitalAlert.objects.filter(
        patient__hospital=hospital,
        status="resolved"
    ).count()
    sla_compliance = round((resolved_alerts / total_alerts) * 100, 1) if total_alerts else 100

    staff_users = CustomUser.objects.filter(hospital=hospital)
    total_staff_count = staff_users.count()
    active_staff_count = staff_users.filter(is_active=True).count()
    inactive_staff_count = total_staff_count - active_staff_count

    if staff_query:
        staff_users = staff_users.filter(
            Q(username__icontains=staff_query)
            | Q(email__icontains=staff_query)
            | Q(first_name__icontains=staff_query)
            | Q(last_name__icontains=staff_query)
            | Q(specialty__icontains=staff_query)
        )

    if staff_role:
        staff_users = staff_users.filter(role=staff_role)

    filtered_staff_count = staff_users.count()
    staff_users = staff_users.order_by("role", "username")
    recent_staff_activity = AuditLog.objects.filter(
        user__hospital=hospital,
        model_name="CustomUser",
    ).select_related("user")[:10]

    return {
        "form": form,
        "staff_users": staff_users,
        "staff_query": staff_query,
        "staff_role": staff_role,
        "staff_role_choices": CustomUser.USER_ROLE_CHOICES,
        "total_staff_count": total_staff_count,
        "active_staff_count": active_staff_count,
        "inactive_staff_count": inactive_staff_count,
        "filtered_staff_count": filtered_staff_count,
        "recent_staff_activity": recent_staff_activity,
        "chart_labels": [entry["month"].strftime("%b %Y") for entry in income_by_month],
        "chart_data": [entry["total"] for entry in income_by_month],
        "patient_count": Patient.objects.filter(hospital=hospital).count(),
        "appointment_count": Appointment.objects.filter(hospital=hospital).count(),
        "bill_count": Bill.objects.filter(hospital=hospital).count(),
        "total_income": total_income,
        "monthly_income": monthly_income,
        "total_alerts": total_alerts,
        "critical_open": critical_open,
        "sla_compliance": sla_compliance,
        "escalations": escalations,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    }


def build_accountant_dashboard_context(user):
    hospital = user.hospital
    nhis = Payer.objects.filter(code="NHIS").first()
    kschma = Payer.objects.filter(code="KSCHMA").first()

    government_bills = Bill.objects.filter(
        hospital=hospital,
        third_party__payer_type__in=["federal", "state"],
    ).select_related("patient", "third_party")

    nhis_bills = government_bills.filter(patient__patientcoverage__payer=nhis).order_by("-created_at")
    kschma_bills = government_bills.filter(patient__patientcoverage__payer=kschma).order_by("-created_at")

    def bill_totals(qs):
        return {
            "total": qs.aggregate(t=Sum("third_party_payable"))["t"] or 0,
            "paid": qs.filter(is_fully_paid=True).aggregate(p=Sum("third_party_payable"))["p"] or 0,
            "unpaid": qs.filter(is_fully_paid=False).aggregate(u=Sum("third_party_payable"))["u"] or 0,
        }

    return {
        "nhis": bill_totals(nhis_bills),
        "kschma": bill_totals(kschma_bills),
        "nhis_bills": nhis_bills[:10],
        "kschma_bills": kschma_bills[:10],
        "government_bill_count": government_bills.count(),
        "unread_count": Message.objects.filter(recipient=user, is_read=False).count(),
    }

@platform_required
def platform_dashboard(request):
    hospitals = Hospital.objects.all().order_by('name')
    total_hospitals = hospitals.count()
    active_hospitals = hospitals.filter(is_active=True).count()
    expired_hospitals = 0
    revenue = 0

    today = timezone.now().date()

    for hospital in hospitals:
        try:
            subscription = hospital.subscription
        except ObjectDoesNotExist:
            subscription = None
        hospital.subscription = subscription

        if subscription and subscription.end_date < today:
            expired_hospitals += 1

        if subscription:
            if subscription.plan == 'basic':
                revenue += 10000
            elif subscription.plan == 'standard':
                revenue += 25000
            elif subscription.plan == 'premium':
                revenue += 50000

    return render(request, 'platform/dashboard.html', {
        'hospitals': hospitals,
        'total_hospitals': total_hospitals,
        'active_hospitals': active_hospitals,
        'expired_hospitals': expired_hospitals,
        'revenue': revenue,
        'today': today,
    })

@platform_required
def toggle_hospital(request, hospital_id):
    hospital = get_object_or_404(Hospital, id=hospital_id)
    hospital.is_active = not hospital.is_active
    hospital.save()
    return redirect('platform_dashboard')

@platform_required
def create_hospital(request):
    if request.method == 'POST':
        form = HospitalCreateForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('platform_dashboard')
    else:
        form = HospitalCreateForm()

    return render(request, 'platform/create_hospital.html', {'form': form})

@platform_required
def view_hospital(request, hospital_id):
    hospital = get_object_or_404(Hospital, id=hospital_id)
    try:
        subscription = hospital.subscription
    except ObjectDoesNotExist:
        subscription = None

    return render(request, 'platform/view_hospital.html', {
        'hospital': hospital,
        'subscription': subscription,
    })

@platform_required
def edit_hospital(request, hospital_id):
    hospital = get_object_or_404(Hospital, id=hospital_id)

    if request.method == 'POST':
        form = HospitalCreateForm(request.POST, request.FILES, instance=hospital)
        if form.is_valid():
            form.save()
            return redirect('platform_dashboard')
    else:
        form = HospitalCreateForm(instance=hospital)

    return render(request, 'platform/edit_hospital.html', {
        'form': form,
        'hospital': hospital,
    })

@platform_required
def payment_page(request):
    return render(request, 'platform/payment.html', {
        'PAYSTACK_PUBLIC_KEY': settings.PAYSTACK_PUBLIC_KEY,
    })

@login_required
def verify_payment(request, reference):
    import requests
    from django.conf import settings

    url = f"https://api.paystack.co/transaction/verify/{reference}"

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    }

    response = requests.get(url, headers=headers)
    data = response.json()

    if data['status'] and data['data']['status'] == 'success':
        # Get hospital via user
        user = request.user
        hospital = user.hospital

        # Activate subscription
        from datetime import date, timedelta

        Subscription.objects.update_or_create(
            hospital=hospital,
            defaults={
                'plan': 'standard',
                'start_date': date.today(),
                'end_date': date.today() + timedelta(days=30),
                'is_active': True
            }
        )

        messages.success(request, "Payment successful! Your subscription has been activated.")
        return redirect('dashboard')

    messages.error(request, "Payment verification failed. Please contact support.")
    return redirect('payment_failed')

@login_required
def payment_failed(request):
    return render(request, 'platform/payment_failed.html')

# =======================================================
# DASHBOARD
# =======================================================
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from datetime import date

@login_required
def dashboard(request):
    user = request.user
    hospital = getattr(user, 'hospital', None)
    
    if not hospital:
        return render(request, "billing/dashboard.html", {"error": "No hospital assigned"})

    # Common context
    unread_count = Message.objects.filter(recipient=user, is_read=False).count()
    base_context = {"unread_count": unread_count}

    # ==============================
    # NURSE DASHBOARD
    # ==============================
    if user.role == "nurse":
        active_visits = PatientVisit.objects.filter(
            hospital=hospital
        ).exclude(status="completed").select_related("patient", "assigned_doctor")
        
        recent_vitals = VitalSign.objects.filter(
            patient__hospital=hospital
        ).select_related("patient").order_by("-created_at")[:20]
        
        return render(request, "billing/dashboard_nurse.html", {
            **base_context,
            "active_visits": active_visits,
            "recent_vitals": recent_vitals,
        })
    

    # ==============================
    # LAB DASHBOARD
    # ==============================
    
    if user.role == "lab":

        today = timezone.localdate()
    
        pending_tests = LabTestRequest.objects.filter(
            hospital=hospital,
            status="requested",
            visit__is_active=True
        )
    
        completed_today = LabTestRequest.objects.filter(
            hospital=hospital,
            status="completed",
            completed_at__date=today
        ).count()
    
        return render(request, "billing/dashboard_lab.html", {
            **base_context,
            "pending_tests": pending_tests,
            "pending_count": pending_tests.count(),
            "completed_today": completed_today
        })
    
    # ==============================
    # RECEPTIONIST DASHBOARD
    # ==============================
        
    if user.role == "radiologist":

        today = timezone.localdate()
    
        pending_scans = RadiologyRequest.objects.filter(
            hospital=hospital,
            status="requested"
        ).select_related("visit__patient", "doctor")
    
        completed_today = RadiologyRequest.objects.filter(
            hospital=hospital,
            status="completed",
            completed_at__date=today
        ).count()
    
        return render(request, "billing/dashboard_radiologist.html", {
            **base_context,
            "pending_scans": pending_scans,
            "total_scans": RadiologyRequest.objects.filter(hospital=hospital).count(),
            "pending_reports": pending_scans.count(),
            "completed_today": completed_today
        })

    # ==============================
    # PHARMACIST DASHBOARD
    # ==============================
    if user.role == "pharmacist":
        today = timezone.localdate()
        prescriptions = (
            Prescription.objects.filter(status="issued", hospital=hospital)
            .select_related("visit__patient", "doctor")
            .order_by("-issued_at")
        )

        today_dispensed = Prescription.objects.filter(
            status="dispensed",
            hospital=hospital,
            dispensed_at__date=today,
        ).count()

        return render(
            request,
            "billing/pharmacist_dashboard.html",
            {
                **base_context,
                "prescriptions": prescriptions,
                "today_dispensed": today_dispensed,
            },
        )
    
    # ==============================
    # RECEPTIONIST DASHBOARD
    # ==============================
   
    if user.role == "receptionist":
        today = date.today()
    
        today_appointments_qs = Appointment.objects.filter(
            hospital=hospital,
            date=today
        )

        checked_in_patient_ids = set(
            PatientVisit.objects.filter(
                hospital=hospital,
                is_active=True,
                patient__appointment__date=today,
            )
            .exclude(status="completed")
            .values_list("patient_id", flat=True)
        )
        walkins = today_appointments_qs.filter(is_walk_in=True).count()
    
        return render(request, "billing/dashboard_receptionist.html", {
            **base_context,
    
            "total_patients": Patient.objects.filter(hospital=hospital).count(),
    
            "today_appointments": today_appointments_qs.count(),
    
            "new_patients": Patient.objects.filter(
                hospital=hospital,
                created_at__date=today
            ).count(),
    
            "checked_in": len(checked_in_patient_ids),
    
            "walkins": walkins,
    
            "unread_messages": unread_count,
    
            "today_appointments_list": today_appointments_qs.select_related(
                "patient",
                "doctor"
            ).order_by("time"),
        })

    # ==============================
    # DOCTOR DASHBOARD
    # ==============================
    if user.role == "doctor":
        query = request.GET.get("q")
        last_30_days = timezone.now() - timedelta(days=30)

        patients = Patient.objects.filter(
            hospital=hospital,
            patientvisit__assigned_doctor=user,
        ).distinct()

        if query:
            patients = patients.filter(
                Q(full_name__icontains=query) |
                Q(phone_number__icontains=query) |
                Q(id__iexact=query)
            )

        active_visits = PatientVisit.objects.filter(
            hospital=hospital,
            assigned_doctor=user,
        ).exclude(status="completed").select_related("patient")

        queue = PatientVisit.objects.filter(
            hospital=hospital,
            assigned_doctor=user,
            status__in=["pending", "under_diagnosis"],
        ).select_related("patient").order_by("-is_emergency", "created_at")

        doctor_prescriptions = Prescription.objects.filter(hospital=hospital, doctor=user)
        prescriptions_issued = doctor_prescriptions.count()
        prescriptions_dispensed = doctor_prescriptions.filter(status="dispensed").count()
        completion_rate = round((prescriptions_dispensed / prescriptions_issued) * 100) if prescriptions_issued else 0
        today_appointments = Appointment.objects.filter(
            hospital=hospital,
            doctor=user,
            date=timezone.localdate(),
        ).select_related("patient")

        return render(request, "billing/dashboard_doctor.html", {
            **base_context,
            "patients": patients[:50],
            "doctor_patient_count": patients.count(),
            "visits_last_30": PatientVisit.objects.filter(
                hospital=hospital,
                assigned_doctor=user,
                created_at__gte=last_30_days,
            ).count(),
            "prescriptions_issued": prescriptions_issued,
            "prescriptions_dispensed": prescriptions_dispensed,
            "completion_rate": completion_rate,
            "active_visits": active_visits,
            "queue": queue,
            "today_appointments": today_appointments,
        })
        
    # ==============================
    # ADMIN DASHBOARD
    # ==============================
    if user.role == "admin":
        return admin_dashboard(request)

    # ==============================
    # PLATFORM ADMIN DASHBOARD
    # ==============================
    if user.role == "platform_admin":
        return platform_dashboard(request)

    # ==============================
    # OTHER ROLES (fallback)
    # ==============================
    template_map = {
        "accountant": "billing/dashboard_accountant.html",
        "lab": "billing/dashboard_lab.html",
        "radiologist": "billing/dashboard_radiologist.html",
        "pharmacist": "billing/pharmacist_dashboard.html",
    }
    
    template = template_map.get(user.role, "billing/dashboard.html")
    return render(request, template, base_context)
        
# =======================================================
# HOME & ROLE REDIRECT
# =======================================================

def home(request):
    return render(request, "home.html")


@login_required
def redirect_by_role(request):
    role_redirects = {
        "platform_admin": "platform_dashboard",
        "admin": "admin_dashboard",
        "doctor": "doctor_dashboard",
        "receptionist": "receptionist_dashboard",
        "accountant": "accountant_dashboard",
        "radiologist": "radiology_dashboard",
        "lab": "lab_dashboard",
        "pharmacist": "pharmacist_dashboard",
    }
    return redirect(role_redirects.get(request.user.role, "dashboard"))


# =======================================================
# PATIENT MANAGEMENT
# =======================================================

@login_required
@login_required
def patient_list(request):
    query = request.GET.get("q", "")
    patients = Patient.objects.filter(hospital=request.user.hospital).order_by("full_name")
    if query:
        # Search by name, phone number, or patient ID
        patients = patients.filter(
            Q(full_name__icontains=query) |
            Q(phone_number__icontains=query) |
            Q(id__iexact=query)
        )
    active_visit_patient_ids = set(
        PatientVisit.objects.filter(
            hospital=request.user.hospital,
            is_active=True,
        ).exclude(status="completed").values_list("patient_id", flat=True)
    )
    for patient in patients:
        patient.active_visit = patient.id in active_visit_patient_ids
    return render(request, "billing/patient_list.html", {"patients": patients, "query": query})


@login_required
def check_patient(request, patient_id):
    patient = hospital_scoped_or_404(Patient, request.user, id=patient_id)
    if not request.user.is_receptionist() and not request.user.is_admin():
        return redirect("patient_emr", patient_id=patient.id)

    active_visit = PatientVisit.objects.filter(
        patient=patient,
        hospital=request.user.hospital,
        is_active=True,
    ).exclude(status="completed").first()

    return render(
        request,
        "billing/check_patient.html",
        {"patient": patient, "active_visit": active_visit},
    )


@login_required
def patient_detail(request, patient_id):
    """Simple patient detail endpoint — redirect based on role."""
    patient = hospital_scoped_or_404(Patient, request.user, id=patient_id)
    if request.user.is_receptionist():
        return redirect("check_patient", patient_id=patient.id)
    return redirect("patient_emr", patient_id=patient.id)


@login_required
def create_patient(request):
    if request.method == "POST":
        name = request.POST.get("name")
        dob = request.POST.get("dob")
        phone = request.POST.get("phone")

        hospital = request.user.hospital
        if not hospital:
            messages.error(request, "No hospital found.")
            return redirect("receptionist_dashboard")

        patient = Patient.objects.create(
            full_name=name,
            date_of_birth=dob,
            phone_number=phone,
            hospital=hospital,
        )
        messages.success(request, "Patient created successfully.")
        return redirect(f"/appointments/create/?patient={patient.id}")
    return render(request, "billing/create_patient.html")


@login_required
def register_patient(request):
    payers = Payer.objects.filter(active=True)

    if request.method == "POST":
        # ensure patient is linked to the user's hospital
        if not getattr(request.user, 'hospital', None):
            messages.error(request, "You are not linked to a hospital.")
            return redirect("receptionist_dashboard")

        # Patient info
        patient = Patient.objects.create(
            full_name=request.POST.get("full_name"),
            date_of_birth=request.POST.get("date_of_birth") or None,
            phone_number=request.POST.get("phone"),
            hospital=request.user.hospital,
        )

        payer = Payer.objects.get(id=request.POST.get("payer"))

        # Default coverage logic
        patient_percentage = 100
        government_percentage = 0

        if payer.code in ["NHIS", "KSCHMA"]:
            patient_percentage = 10
            government_percentage = 90

        if payer.code == "HOSPITAL_FREE":
            patient_percentage = 0
            government_percentage = 100

        PatientCoverage.objects.create(
            patient=patient,
            payer=payer,
            patient_percentage=patient_percentage,
            government_percentage=government_percentage,
            approved_by=request.user,
            notes=request.POST.get("coverage_notes", "")
        )

        return redirect("receptionist_dashboard")

    return render(request, "billing/register_patient.html", {"payers": payers})


# =======================================================
# APPOINTMENTS
# =======================================================

@login_required
def appointment_list(request):
    hospital = getattr(request.user, "hospital", None)
    if not hospital:
        messages.error(request, "You are not linked to any hospital.")
        return redirect("dashboard")

    query = request.GET.get("q", "")
    appointments = Appointment.objects.filter(hospital=hospital)
    if query:
        appointments = appointments.filter(
            Q(patient__full_name__icontains=query) |
            Q(patient__phone_number__icontains=query) |
            Q(patient__id__iexact=query) |
            Q(reason__icontains=query)
        )
    appointments = appointments.select_related("patient", "doctor").order_by("-date", "-time")
    return render(request, "billing/appointment_list.html", {"appointments": appointments, "query": query})


@login_required
def create_appointment(request):
    hospital = request.user.hospital
    patients = Patient.objects.filter(hospital=hospital)
    doctors = CustomUser.objects.filter(
        role="doctor",
        hospital=hospital,
        is_active=True,
    ).order_by("first_name", "last_name", "username")
    preselected_patient_id = request.GET.get("patient")

    if request.method == "POST":
        patient_id = request.POST.get("patient")
        doctor_id = request.POST.get("doctor")
        date = request.POST.get("date")
        time = request.POST.get("time")
        reason = request.POST.get("reason")

        patient = get_object_or_404(Patient, id=patient_id, hospital=hospital)
        doctor = get_object_or_404(
            CustomUser,
            id=doctor_id,
            hospital=hospital,
            role="doctor",
            is_active=True,
        )

        try:
            Appointment.objects.create(
                hospital=hospital,
                patient=patient,
                doctor=doctor,
                date=date,
                time=time,
                reason=reason,
                status="scheduled",
            )
        except Exception:
            messages.error(
                request,
                "That doctor already has an appointment at the selected date and time.",
            )
        else:
            messages.success(request, "Appointment created successfully.")
            return redirect("appointment_list")

    return render(
        request,
        "billing/create_appointment.html",
        {
            "patients": patients,
            "doctors": doctors,
            "preselected_patient_id": preselected_patient_id,
        },
    )


# =======================================================
# BILLING & PAYMENTS
# =======================================================

@login_required
def bill_list(request):
    hospital = request.user.hospital
    if request.user.role in ["accountant", "admin"]:
        bills = Bill.objects.filter(hospital=hospital)
    elif request.user.role == "receptionist":
        bills = Bill.objects.filter(hospital=hospital, bill_type="front_desk", created_by=request.user)
    elif request.user.role == "pharmacist":
        bills = Bill.objects.filter(hospital=hospital, bill_type="pharmacy", created_by=request.user)
    else:
        bills = Bill.objects.none()

    bills = bills.select_related('patient').order_by('-created_at')
    return render(request, "billing/bill_list.html", {"bills": bills})

@login_required
def create_bill_index(request):
    if not user_can_create_front_desk_bill(request.user):
        messages.error(request, "Only receptionists and administrators may create front desk bills.")
        return redirect("dashboard")
    messages.info(request, "Please select a patient to create a front desk bill.")
    return redirect("patient_list")

@login_required
def create_bill(request, patient_id):
    if not user_can_create_front_desk_bill(request.user):
        messages.error(request, "Only receptionists and administrators may create front desk bills.")
        return redirect("dashboard")

    patient = hospital_scoped_or_404(Patient, request.user, id=patient_id)
    services = Service.objects.filter(hospital=patient.hospital)
    # include patient coverage info in template context
    coverage = getattr(patient, "patientcoverage", None)

    if request.method == "POST":
        items_data, total = [], 0
        service_ids = request.POST.getlist("service")

        for service_id in service_ids:
            service = get_object_or_404(Service, id=service_id, hospital=patient.hospital)
            qty = int(request.POST.get(f"quantity_{service_id}", 1))
            if qty <= 0:
                qty = 1
            subtotal = service.price * qty
            total += subtotal
            items_data.append({"service": service, "quantity": qty, "subtotal": subtotal})

        if not items_data:
            messages.error(request, "Please select at least one service before generating a bill.")
            return render(request, "billing/create_bill.html", {"patient": patient, "services": services, "coverage": coverage})

        patient_payable, third_party_payable, third_party = calculate_bill_split(patient, total)

        bill = Bill.objects.create(
            patient=patient,
            total_amount=total,
            created_by=request.user,
            hospital=patient.hospital,
            patient_payable=patient_payable,
            third_party_payable=third_party_payable,
            third_party=third_party,
            bill_type="front_desk",
        )
        log_action(request.user, "create", "Bill", bill.id, f"Created bill of {total} for {patient}")

        for item in items_data:
            BillItem.objects.create(
                bill=bill,
                service=item["service"],
                quantity=item["quantity"],
                subtotal=item["subtotal"],
            )

        return redirect("view_invoice", bill_id=bill.id)

    return render(
        request,
        "billing/create_bill.html",
        {
            "patient": patient,
            "services": services,
            "coverage": coverage,
        },
    )


@login_required
def create_pharmacy_bill(request, patient_id):
    if not user_can_create_pharmacy_bill(request.user):
        messages.error(request, "Only pharmacists and administrators may create pharmacy bills.")
        return redirect("dashboard")

    patient = hospital_scoped_or_404(Patient, request.user, id=patient_id)
    medicines = Medicine.objects.filter(hospital=patient.hospital)
    coverage = getattr(patient, "patientcoverage", None)

    if request.method == "POST":
        items_data, total = [], 0
        medicine_ids = request.POST.getlist("medicine")

        for medicine_id in medicine_ids:
            medicine = get_object_or_404(Medicine, id=medicine_id, hospital=patient.hospital)
            qty = int(request.POST.get(f"quantity_{medicine_id}", 1))
            if qty <= 0:
                qty = 1
            subtotal = medicine.price * qty
            total += subtotal
            items_data.append({"medicine": medicine, "quantity": qty, "subtotal": subtotal})

        if not items_data:
            messages.error(request, "Please select at least one medicine before generating a pharmacy bill.")
            return render(request, "billing/create_pharmacy_bill.html", {"patient": patient, "medicines": medicines, "coverage": coverage})

        patient_payable, third_party_payable, third_party = calculate_bill_split(patient, total)

        bill = Bill.objects.create(
            patient=patient,
            total_amount=total,
            created_by=request.user,
            hospital=patient.hospital,
            patient_payable=patient_payable,
            third_party_payable=third_party_payable,
            third_party=third_party,
            bill_type="pharmacy",
        )
        log_action(request.user, "create", "Bill", bill.id, f"Created pharmacy bill of {total} for {patient}")

        for item in items_data:
            BillItem.objects.create(
                bill=bill,
                medicine=item["medicine"],
                quantity=item["quantity"],
                subtotal=item["subtotal"],
            )

        return redirect("view_invoice", bill_id=bill.id)

    return render(request, "billing/create_pharmacy_bill.html", {
        "patient": patient,
        "medicines": medicines,
        "coverage": coverage,
    })


@login_required
def view_invoice(request, bill_id):
    bill = hospital_scoped_or_404(Bill, request.user, id=bill_id)
    if not user_can_view_bill(request.user, bill):
        messages.error(request, "Unauthorized access to this invoice.")
        return redirect("dashboard")

    items = bill.items.all()
    payments = bill.payment_set.all()
    paid = sum(p.amount_paid for p in payments)
    due = bill.total_amount - paid
    return render(
        request,
        "billing/invoice.html",
        {"bill": bill, "items": items, "payments": payments, "paid": paid, "due": due},
    )


@login_required
def download_invoice_pdf(request, bill_id):
    bill = hospital_scoped_or_404(Bill, request.user, id=bill_id)
    if not user_can_view_bill(request.user, bill):
        messages.error(request, "Unauthorized access to this invoice.")
        return redirect("dashboard")

    items = bill.items.all()
    payments = bill.payment_set.all()
    paid = sum(p.amount_paid for p in payments)
    due = bill.total_amount - paid

    html = get_template("billing/invoice.html").render(
        {"bill": bill, "items": items, "payments": payments, "paid": paid, "due": due}
    )
    buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=buffer, encoding="UTF-8")
    buffer.seek(0)

    if pisa_status.err:
        return HttpResponse("PDF generation error", status=500)

    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="invoice_{bill.invoice_no}.pdf"'
    return response


@login_required
def record_payment(request, bill_id):
    bill = hospital_scoped_or_404(Bill, request.user, id=bill_id)
    if not user_can_record_payment(request.user):
        messages.error(request, "Only accountants and administrators may record final payments.")
        return redirect("dashboard")

    if request.method == "POST":
        amount = request.POST.get("amount")
        payment_method = request.POST.get("payment_method")
        Payment.objects.create(
            bill=bill,
            amount_paid=amount,
            payment_mode=payment_method,
            hospital=bill.hospital,
        )

        # Update bill status — only consider the patient's portion
        total_paid = bill.payment_set.aggregate(total=Sum('amount_paid'))['total'] or 0
        if total_paid >= bill.patient_payable:
            bill.is_fully_paid = True
            bill.is_finalized = True
            bill.finalized_at = timezone.now()
            bill.approved_by = request.user
            bill.save()

        messages.success(request, "Payment recorded successfully.")
        return redirect("view_invoice", bill_id=bill.id)
    return render(request, "billing/record_payment.html", {"bill": bill})


# =======================================================
# REPORTS & AUDIT LOGS
# =======================================================

@login_required
def income_report(request):
    hospital_filter = {}
    if hasattr(request.user, 'hospital') and request.user.hospital:
        hospital_filter = {"hospital": request.user.hospital}

    payments = Payment.objects.filter(**hospital_filter).order_by("-paid_on")
    total_income = payments.aggregate(total=Sum("amount_paid"))["total"] or 0

    monthly_data = (
        payments.annotate(month=TruncMonth("paid_on"))
        .values("month")
        .annotate(total=Sum("amount_paid"))
        .order_by("month")
    )
    labels = [item["month"].strftime("%B %Y") for item in monthly_data]
    data = [float(item["total"]) for item in monthly_data]

    return render(
        request,
        "billing/income_report.html",
        {"payments": payments, "total_income": total_income, "labels": labels, "data": data},
    )


@login_required
def audit_logs(request):
    if request.user.role not in ["admin", "accountant"]:
        return HttpResponseForbidden("You are not authorized to view this page.")
    logs = AuditLog.objects.all().order_by("-timestamp")
    return render(request, "billing/audit_logs.html", {"logs": logs})


# =======================================================
# MESSAGING
# =======================================================

# billing/views.py  (or move to messaging/views.py if preferred)

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden
from django.db.models import Q, Max
from django.contrib.auth import get_user_model

from messaging.models import Message
from messaging.forms import MessageForm

User = get_user_model()


# 🔒 Utility: enforce same hospital
def _validate_same_hospital(request, user):
    if user.hospital != request.hospital:
        return False
    return True


@login_required
def compose_message(request):
    hospital = getattr(request.user, "hospital", None)
    if not hospital:
        return HttpResponseForbidden("Invalid hospital domain.")

    to_user_id = request.GET.get("to")
    reply_subject = request.GET.get("subject")
    initial_data = {}

    if to_user_id:
        recipient = User.objects.filter(
            id=to_user_id,
            hospital=hospital
        ).first()
        if recipient:
            initial_data["recipient"] = recipient

    if reply_subject and not reply_subject.lower().startswith("re:"):
        initial_data["subject"] = f"Re: {reply_subject}"

    if request.method == "POST":
        # ✅ PASS HOSPITAL TO FORM
        form = MessageForm(request.POST, hospital=hospital)
        if form.is_valid():
            msg = form.save(commit=False)
            msg.sender = request.user
            msg.hospital = hospital
            msg.save()
            return redirect("inbox")
    else:
        # ✅ PASS HOSPITAL TO FORM
        form = MessageForm(initial=initial_data, hospital=hospital)

    return render(request, "billing/messages/compose.html", {"form": form})

@login_required
def inbox(request):
    hospital = request.hospital

    subquery = (
        Message.objects.filter(
            hospital=hospital,
            recipient=request.user
        )
        .values("sender")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    messages = (
        Message.objects.filter(
            id__in=subquery,
            hospital=hospital
        )
        .select_related("sender")
        .order_by("-timestamp")
    )

    return render(request, "billing/messages/inbox.html", {"messages": messages})


@login_required
def conversation(request, sender_id):
    hospital = request.hospital

    sender = get_object_or_404(
        User,
        id=sender_id,
        hospital=hospital
    )

    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        if body:
            Message.objects.create(
                hospital=hospital,
                sender=request.user,
                recipient=sender,
                subject="Reply",
                body=body,
            )
            return redirect("conversation", sender_id=sender.id)

    msgs = Message.objects.filter(
        hospital=hospital
    ).filter(
        Q(sender=request.user, recipient=sender) |
        Q(sender=sender, recipient=request.user)
    ).order_by("timestamp")

    Message.objects.filter(
        hospital=hospital,
        sender=sender,
        recipient=request.user,
        is_read=False
    ).update(is_read=True)

    return render(
        request,
        "billing/messages/conversation.html",
        {"sender": sender, "messages": msgs},
    )


@login_required
def sent_messages(request):
    hospital = request.hospital

    messages_sent = Message.objects.filter(
        hospital=hospital,
        sender=request.user
    ).order_by("-timestamp")

    return render(
        request,
        "billing/messages/sent_messages.html",
        {"messages_sent": messages_sent},
    )


@login_required
def message_detail(request, pk):
    hospital = request.hospital

    message = get_object_or_404(
        Message,
        id=pk,
        hospital=hospital
    )

    # 🔒 Must be sender or recipient
    if message.sender != request.user and message.recipient != request.user:
        return HttpResponseForbidden("Unauthorized.")

    if message.recipient == request.user and not message.is_read:
        message.is_read = True
        message.save(update_fields=["is_read"])

    return render(
        request,
        "billing/messages/message_detail.html",
        {"message": message}
    )


# =======================================================
# MEDICAL RECORDS / REPORTS
# =======================================================
# =======================================================
# 🩺 PATIENT EMR & MEDICAL RECORDS
# =======================================================

@login_required
def patient_emr(request, patient_id):

    patient = get_object_or_404(
        Patient,
        id=patient_id,
        hospital=request.user.hospital
    )

    # ==============================
    # VISITS (hospital isolated)
    # ==============================
    visits = PatientVisit.objects.filter(
        patient=patient,
        hospital=request.user.hospital
    ).order_by("-created_at", "-id")

    # Active visit
    visit_id = request.GET.get("visit")

    if visit_id:
        active_visit = get_object_or_404(
            PatientVisit,
            id=visit_id,
            patient=patient,
            hospital=request.user.hospital
        )
    else:
        active_visit = visits.filter(
            is_active=True
        ).exclude(
            status="completed"
        ).first()

    # ==============================
    # LAB + RADIOLOGY (UPDATED MODELS)
    # ==============================
    lab_tests = LabTestRequest.objects.filter(
        visit__patient=patient,
        hospital=request.user.hospital
    ).select_related("visit", "doctor").order_by("-requested_at")

    lab_reports = LabReport.objects.filter(
        patient=patient
    ).select_related("lab_technician").order_by("-date")

    radiology_requests = RadiologyRequest.objects.filter(
        visit__patient=patient,
        hospital=request.user.hospital
    ).select_related("visit", "doctor").order_by("-requested_at")

    radiology_reports = RadiologyReport.objects.filter(
        patient=patient
    ).select_related("radiologist").order_by("-created_at")

    # ==============================
    # NOTES / RECORDS
    # ==============================
    medical_records = MedicalRecord.objects.filter(
        patient=patient,
        patient__hospital=request.user.hospital
    ).order_by("-created_at")

    notes = ConsultationNote.objects.filter(
        patient=patient
    ).order_by("-created_at")

    # ==============================
    # PRESCRIPTIONS
    # ==============================
    prescriptions = Prescription.objects.filter(
        visit__patient=patient,
        hospital=request.user.hospital
    ).select_related("doctor", "visit").order_by("-issued_at")

    # ==============================
    # VITAL SIGNS
    # ==============================
    vital_signs = VitalSign.objects.filter(
        patient=patient,
        patient__hospital=request.user.hospital
    ).select_related("recorded_by").order_by("-created_at")[:30]

    latest_vitals = vital_signs.first()
    vitals_status = evaluate_vitals(latest_vitals) if latest_vitals else {}

    # ==============================
    # ALERTS
    # ==============================
    alert = None
    if alert_id := request.GET.get("alert"):
        alert = VitalAlert.objects.filter(
            id=alert_id,
            patient=patient,
            patient__hospital=request.user.hospital
        ).first()

    vital_alerts = VitalAlert.objects.filter(
        patient=patient,
        patient__hospital=request.user.hospital
    ).exclude(status="resolved").prefetch_related("logs__performed_by").order_by("-created_at")

    latest_medical_record = medical_records.first()
    latest_note = notes.first()

    summary = {
        "record_count": medical_records.count(),
        "lab_count": lab_tests.count(),
        "lab_report_count": lab_reports.count(),
        "radiology_count": radiology_requests.count(),
        "radiology_report_count": radiology_reports.count(),
        "prescription_count": prescriptions.count(),
        "visit_count": visits.count(),
    }

    # ==============================
    # CONTEXT
    # ==============================
    context = {
        "patient": patient,
        "visits": visits,
        "active_visit": active_visit,

        "medical_records": medical_records,
        "notes": notes,

        "lab_tests": lab_tests,
        "lab_reports": lab_reports,
        "radiology_requests": radiology_requests,
        "radiology_reports": radiology_reports,

        "prescriptions": prescriptions,

        "vital_signs": vital_signs,
        "latest_vitals": latest_vitals,
        "vitals_status": vitals_status,
        "vital_alerts": vital_alerts,
        "linked_alert": alert,
        "latest_medical_record": latest_medical_record,
        "latest_note": latest_note,
        "summary": summary,

        "unread_count": Message.objects.filter(
            recipient=request.user,
            is_read=False
        ).count(),
    }

    return render(request, "billing/patient_emr.html", context)

@login_required
def patient_history(request, patient_id):
    """Historical medical records for a patient with optimized database hits"""
    patient = get_object_or_404(
        Patient, 
        id=patient_id, 
        hospital=request.user.hospital
    )
    
    # Using select_related('doctor') makes the template load much faster
    # because it fetches the doctor's name in the same query.
    history = MedicalRecord.objects.filter(
        patient=patient,
        patient__hospital=request.user.hospital
    ).select_related('doctor').order_by("-created_at")
    
    unread_messages = Message.objects.filter(
        recipient=request.user, 
        is_read=False
    ).count()

    return render(request, "billing/patient_history.html", {
        "patient": patient,
        "history": history,
        "unread_count": unread_messages,
    })

@login_required
def add_medical_record(request, patient_id):
    """Add clinical note with optional vital alert resolution"""
    # Check role (using .role or .is_doctor depending on your model)
    is_doctor = getattr(request.user, 'role', '') == 'doctor' or (hasattr(request.user, 'is_doctor') and request.user.is_doctor())
    
    if not is_doctor:
        messages.error(request, "Only doctors can add medical notes.")
        return redirect("patient_emr", patient_id=patient_id)
    
    patient = get_object_or_404(Patient, id=patient_id, hospital=request.user.hospital)
    
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        notes = request.POST.get("notes", "").strip()
        alert_id = request.POST.get("alert_id")
        visit_id = request.POST.get("visit_id")
        
        if not title or not notes:
            messages.error(request, "Title and notes are required.")
            return redirect("patient_emr", patient_id=patient.id)

        visit = None
        if visit_id:
            visit = get_object_or_404(
                PatientVisit,
                id=visit_id,
                patient=patient,
                hospital=request.user.hospital,
            )
        
        # Create medical record 
        MedicalRecord.objects.create(
            patient=patient,
            visit=visit,
            doctor=request.user,
            notes=notes,
            note_type="doctor_note",
            diagnosis=title,
            treatment="See notes"
        )
        
        # Resolve alert if linked
        if alert_id:
            alert = VitalAlert.objects.filter(
                id=alert_id,
                patient=patient,
                patient__hospital=request.user.hospital
            ).first()
            
            if alert:
                alert.status = "resolved"
                alert.resolved_at = timezone.now()
                alert.save()
                
                VitalAlertLog.objects.create(
                    alert=alert,
                    action="resolved",
                    performed_by=request.user,
                    notes="Resolved via consultation note"
                )
        
        messages.success(request, "Medical note added successfully.")
        return redirect("patient_emr", patient_id=patient.id)
    
    return redirect("patient_emr", patient_id=patient_id)

@login_required
def add_doctor_note(request, patient_id):
    """Quick doctor note addition (legacy support)"""
    if not request.user.is_doctor():
        messages.error(request, "Only doctors can add notes.")
        return redirect("patient_emr", patient_id=patient_id)
    
    patient = get_object_or_404(Patient, id=patient_id, hospital=request.user.hospital)
    visits = PatientVisit.objects.filter(
        patient=patient,
        hospital=request.user.hospital,
        is_active=True,
    ).exclude(status="completed").order_by("-created_at")
    
    if request.method == "POST":
        visit_id = request.POST.get("visit_id")
        notes = request.POST.get("notes", "").strip()
        
        if not notes:
            messages.error(request, "Notes cannot be empty.")
            return redirect("add_doctor_note", patient_id=patient.id)
        
        visit = get_object_or_404(PatientVisit, id=visit_id, patient=patient, hospital=request.user.hospital)
        
        MedicalRecord.objects.create(
            patient=patient,
            visit=visit,
            doctor=request.user,
            notes=notes,
            note_type="doctor_note",
            diagnosis="Doctor Note",
            treatment="See notes",
        )
        
        messages.success(request, "Doctor note added successfully.")
        return redirect("patient_emr", patient_id=patient.id)
    
    return render(request, "billing/doctor_note_add.html", {
        "patient": patient,
        "visits": visits,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def add_emr_note(request, patient_id):
    """Simplified EMR note addition endpoint"""
    if not request.user.is_doctor():
        messages.error(request, "Only doctors can add notes.")
        return redirect("patient_emr", patient_id=patient_id)
    
    patient = get_object_or_404(Patient, id=patient_id, hospital=request.user.hospital)
    
    if request.method == "POST":
        notes = request.POST.get("notes", "").strip()
        if notes:
            active_visit = (
                PatientVisit.objects.filter(
                    patient=patient,
                    hospital=request.user.hospital,
                    is_active=True,
                )
                .exclude(status="completed")
                .first()
            )
            MedicalRecord.objects.create(
                patient=patient,
                visit=active_visit,
                doctor=request.user,
                notes=notes,
                note_type="doctor_note",
                diagnosis="Doctor Note",
                treatment="See notes",
            )
            messages.success(request, "Doctor note added.")
    
    return redirect("patient_emr", patient_id=patient.id)


@login_required
def export_emr_pdf(request, patient_id):
    """Generate printable PDF of complete patient EMR"""
    patient = get_object_or_404(Patient, id=patient_id, hospital=request.user.hospital)
    
    context = {
        "patient": patient,
        "medical_records": MedicalRecord.objects.filter(patient=patient).order_by("-created_at"),
       "lab_tests": LabTestRequest.objects.filter(
            visit__patient=patient
        ).order_by("-requested_at"),
        
        "radiology_requests": RadiologyRequest.objects.filter(
            visit__patient=patient
        ).order_by("-requested_at"),
        "prescriptions": Prescription.objects.filter(visit__patient=patient).order_by("-issued_at"),
        "vital_signs": VitalSign.objects.filter(patient=patient).order_by("-created_at")[:20],
    }
    
    html_string = render_to_string("billing/print_emr.html", context)
    
    # Try WeasyPrint first (better quality)
    try:
        from weasyprint import HTML
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            HTML(string=html_string).write_pdf(tmp.name)
            tmp.seek(0)
            pdf_data = tmp.read()
    except Exception:
        # Fallback to xhtml2pdf
        buffer = BytesIO()
        pisa_status = pisa.CreatePDF(html_string, dest=buffer, encoding="UTF-8")
        if pisa_status.err:
            return HttpResponse("PDF generation failed", status=500)
        buffer.seek(0)
        pdf_data = buffer.getvalue()
    
    response = HttpResponse(pdf_data, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="EMR_{patient.full_name}_{timezone.now().date()}.pdf"'
    return response


@login_required
def activate_visit(request, visit_id):
    visit = get_object_or_404(
        PatientVisit,
        id=visit_id,
        hospital=request.user.hospital
    )

    visit.is_active = True
    visit.status = "under_diagnosis"
    visit.save(update_fields=["is_active", "status"])

    return redirect("dashboard")


@login_required
def check_in_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related("patient", "doctor"),
        id=appointment_id,
        hospital=request.user.hospital,
    )

    if request.method != "POST":
        return redirect("dashboard")

    if request.user.role not in ["receptionist", "admin"]:
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    today = timezone.localdate()
    if appointment.date > today:
        messages.error(request, "You cannot check in a future appointment yet.")
        return redirect("dashboard")

    if appointment.status == "cancelled":
        messages.error(request, "Cancelled appointments cannot be checked in.")
        return redirect("dashboard")

    visit, created = ensure_active_visit_for_appointment(appointment, request.user)
    if created:
        messages.success(request, f"Visit started for {appointment.patient.full_name}.")
    else:
        messages.info(request, f"{appointment.patient.full_name} already has an active visit.")

    return redirect(f"{reverse('patient_emr', args=[appointment.patient.id])}?visit={visit.id}")

# =======================================================
# 🔬 LAB & RADIOLOGY REPORTS
# =======================================================

@login_required
def add_lab_report(request, patient_id):
    """Create new lab test report"""
    if not request.user.is_lab():
        messages.error(request, "Only lab technicians can add lab reports.")
        return redirect("patient_emr", patient_id=patient_id)
    
    patient = get_object_or_404(Patient, id=patient_id, hospital=request.user.hospital)
    
    if request.method == "POST":
        form = LabReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.patient = patient
            report.lab_technician = request.user
            report.hospital = request.user.hospital
            report.save()
            messages.success(request, "Lab report added successfully.")
            return redirect("patient_emr", patient_id=patient.id)
    else:
        form = LabReportForm()
    
    return render(request, "billing/add_lab_report.html", {
        "form": form,
        "patient": patient,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def add_radiology_report(request, patient_id):
    """Create new radiology report"""
    if not request.user.is_radiologist():
        messages.error(request, "Only radiologists can add radiology reports.")
        return redirect("patient_emr", patient_id=patient_id)
    
    patient = get_object_or_404(Patient, id=patient_id, hospital=request.user.hospital)
    
    if request.method == "POST":
        form = RadiologyReportForm(request.POST, patient=patient)
        if form.is_valid():
            report = form.save(commit=False)
            report.patient = patient
            report.radiologist = request.user
            report.hospital = request.user.hospital
            report.save()
            messages.success(request, "Radiology report added successfully.")
            return redirect("patient_emr", patient_id=patient.id)
        messages.error(request, f"Form errors: {form.errors}")
    else:
        form = RadiologyReportForm(patient=patient)
    
    past_reports = RadiologyReport.objects.filter(
        patient=patient,
        hospital=request.user.hospital
    ).order_by("-created_at")
    
    return render(request, "billing/add_radiology_report.html", {
        "form": form,
        "patient": patient,
        "past_reports": past_reports,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def complete_lab_test(request, test_id):
    test = hospital_scoped_or_404(LabTestRequest, request.user, id=test_id)

    if request.method == "POST":

        result = request.POST.get("result")

        test.result = result
        test.status = "completed"
        test.completed_at = timezone.now()
        test.lab_technician = request.user
        test.save()

        return redirect("dashboard")

    return render(request, "billing/complete_lab_test.html", {"test": test})
    
from django.shortcuts import get_object_or_404, redirect
from .models import LabTestRequest, PatientVisit, Patient



@login_required
def order_lab_test(request, patient_id):
    patient = hospital_scoped_or_404(Patient, request.user, id=patient_id)

    # find active visit
    visit = PatientVisit.objects.filter(
        patient=patient,
        is_active=True
    ).first()

    if not visit:
        messages.error(request, "Patient has no active visit. Please start a visit first.")
        return redirect("patient_emr", patient_id=patient.id)

    if request.method == "POST":

        test_type = request.POST.get("test_type")

        LabTestRequest.objects.create(
            hospital=patient.hospital,
            visit=visit,
            doctor=request.user,
            test_type=test_type,
            status="requested"
        )

        messages.success(request, "Lab test ordered successfully")

    return redirect("patient_emr", patient_id=patient.id)
    
@login_required
def order_radiology(request, visit_id):
    visit = PatientVisit.objects.get(id=visit_id)
    
    if request.method == "POST":
        imaging_type = request.POST.get("imaging_type")
        notes = request.POST.get("notes")

        if not imaging_type:
            messages.error(request, "Imaging type is required")
            return redirect("patient_emr", patient_id=visit.patient.id)

        RadiologyRequest.objects.create(
            hospital=visit.hospital,
            visit=visit,
            doctor=request.user,
            imaging_type=imaging_type,
            notes=notes
        )

        # ✅ update visit status
        visit.status = "radiology_requested"
        visit.save()

        messages.success(request, "Radiology test ordered successfully")
        return redirect("patient_emr", patient_id=visit.patient.id)

    return redirect("patient_emr", patient_id=visit.patient.id)


@login_required
def complete_radiology(request, id):
    scan = RadiologyRequest.objects.get(id=id)

    if request.method == "POST":
        findings = request.POST.get("findings")

        scan.findings = findings
        scan.status = "completed"
        scan.completed_at = timezone.now()
        scan.radiologist = request.user
        scan.save()

        scan.visit.status = "radiology_completed"
        scan.visit.save()

        return redirect("dashboard")


@login_required
def upload_radiology_result(request, request_id):
    radiology = RadiologyRequest.objects.get(id=request_id)

    if request.method == "POST":
        radiology.findings = request.POST.get("findings")
        radiology.scan_image = request.FILES.get("scan_image")
        radiology.status = "completed"
        radiology.completed_at = timezone.now()
        radiology.radiologist = request.user
        radiology.save()

        # Update visit status to radiology_completed
        radiology.visit.status = "radiology_completed"
        radiology.visit.save()

        messages.success(request, "Radiology report uploaded successfully.")
        return redirect("dashboard")

    return render(request, "billing/upload_radiology.html", {
        "radiology": radiology
    })

# =======================================================
# 💊 PRESCRIPTIONS
# =======================================================

@login_required
def create_prescription(request, visit_id):
    """Create new prescription for a patient visit"""
    if not request.user.is_doctor():
        messages.error(request, "Only doctors can create prescriptions.")
        return redirect("dashboard")
    
    visit = get_object_or_404(PatientVisit, id=visit_id, hospital=request.user.hospital)
    
    if request.method == "POST":
        form = PrescriptionForm(request.POST)
        if form.is_valid():
            prescription = form.save(commit=False)
            prescription.hospital = request.user.hospital
            prescription.doctor = request.user
            prescription.visit = visit
            prescription.save()
            messages.success(request, "Prescription created successfully.")
            return redirect("patient_emr", patient_id=visit.patient.id)
    else:
        form = PrescriptionForm(initial={"visit": visit, "doctor": request.user})
    
    return render(request, "billing/prescriptions/create_prescription.html", {
        "form": form,
        "visit": visit,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def pending_prescriptions(request):
    """List of prescriptions awaiting dispensing"""
    if not request.user.is_pharmacist():
        messages.error(request, "Only pharmacists can view pending prescriptions.")
        return redirect("dashboard")
    
    prescriptions = Prescription.objects.filter(
        status="issued",
        hospital=request.user.hospital
    ).select_related("visit__patient", "doctor").order_by("-issued_at")
    
    return render(request, "billing/prescriptions/pending_prescriptions.html", {
        "prescriptions": prescriptions,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def dispense_prescription(request, prescription_id):
    """Mark prescription as dispensed by pharmacist"""
    if not request.user.is_pharmacist():
        messages.error(request, "Only pharmacists can dispense prescriptions.")
        return redirect("dashboard")
    
    prescription = get_object_or_404(
        Prescription, 
        id=prescription_id, 
        hospital=request.user.hospital
    )
    
    if prescription.status == "issued":
        prescription.status = "dispensed"
        prescription.dispensed_at = timezone.now()
        prescription.pharmacist = request.user
        prescription.save()
        messages.success(request, "Prescription marked as dispensed.")
    else:
        messages.warning(request, "This prescription was already dispensed.")
    
    return redirect("pending_prescriptions")


@login_required
def print_visit_prescriptions(request, visit_id):
    """Print-friendly prescription list for a visit"""
    visit = get_object_or_404(PatientVisit, id=visit_id, hospital=request.user.hospital)
    prescriptions = Prescription.objects.filter(
        visit=visit,
        hospital=request.user.hospital
    ).select_related("doctor")
    
    return render(request, "billing/print_visit_prescriptions.html", {
        "visit": visit,
        "prescriptions": prescriptions,
    })


# =======================================================
# ⚠️ VITAL ALERTS & SLA DASHBOARDS
# =======================================================

@login_required
def acknowledge_vital_alert(request, alert_id):
    """Doctor acknowledges a vital sign alert"""
    if not request.user.is_doctor():
        messages.error(request, "Only doctors can acknowledge alerts.")
        return redirect("dashboard")
    
    alert = get_object_or_404(
        VitalAlert,
        id=alert_id,
        patient__hospital=request.user.hospital
    )
    
    if alert.status not in ["open", "escalated"]:
        messages.warning(request, "Alert already acknowledged or resolved.")
        return redirect("patient_emr", patient_id=alert.patient.id)
    
    alert.status = "acknowledged"
    alert.doctor = request.user
    alert.acknowledged_at = timezone.now()
    alert.escalation_deadline = None
    alert.save()
    
    VitalAlertLog.objects.create(
        alert=alert,
        action="acknowledged",
        performed_by=request.user,
        notes="Doctor acknowledged alert via dashboard"
    )
    
    messages.success(request, "Alert acknowledged successfully.")
    return redirect("patient_emr", patient_id=alert.patient.id)


@login_required
def resolve_vital_alert(request, alert_id):
    """Doctor resolves a vital sign alert with clinical notes"""
    if not request.user.is_doctor():
        messages.error(request, "Only doctors can resolve alerts.")
        return redirect("dashboard")
    
    alert = get_object_or_404(
        VitalAlert,
        id=alert_id,
        patient__hospital=request.user.hospital
    )
    
    if alert.status == "resolved":
        messages.warning(request, "Alert already resolved.")
        return redirect("patient_emr", patient_id=alert.patient.id)
    
    if request.method == "POST":
        notes = request.POST.get("notes", "").strip()
        if not notes:
            messages.error(request, "Resolution notes are required.")
            return render(request, "billing/resolve_alert.html", {
                "alert": alert,
                "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
            })
        
        alert.status = "resolved"
        alert.resolved_at = timezone.now()
        alert.save()
        
        VitalAlertLog.objects.create(
            alert=alert,
            action="resolved",
            performed_by=request.user,
            notes=notes
        )
        
        messages.success(request, "Alert resolved with clinical notes.")
        return redirect("patient_emr", patient_id=alert.patient.id)
    
    return render(request, "billing/resolve_alert.html", {
        "alert": alert,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def doctor_alert_dashboard(request):
    """Doctor's personal alert monitoring dashboard"""
    if not request.user.is_doctor():
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    alerts = VitalAlert.objects.filter(
        status__in=["open", "acknowledged", "escalated"],
        patient__hospital=request.user.hospital
    ).select_related("patient", "vital_sign").order_by("-created_at")
    
    for alert in alerts:
        alert.sla_remaining = sla_remaining_time(alert)
        alert.sla_state = sla_timer_state(alert)
    
    return render(request, "billing/doctor_alert_dashboard.html", {
        "alerts": alerts,
        "now": timezone.now(),
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def admin_alert_dashboard(request):
    """Hospital admin's comprehensive alert monitoring"""
    if not request.user.is_admin():
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    alerts = VitalAlert.objects.filter(
        status__in=["open", "escalated"],
        patient__hospital=request.user.hospital
    ).select_related(
        "patient", "vital_sign", "doctor"
    ).order_by("-created_at")
    
    return render(request, "billing/alerts/admin_dashboard.html", {
        "alerts": alerts,
        "now": timezone.now(),
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def doctor_sla_dashboard(request):
    """Admin view of all doctors' SLA compliance metrics"""
    if not request.user.is_admin():
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    hospital = request.user.hospital
    doctors = doctor_sla_metrics(hospital)  # Returns list of doctor metrics dicts
    
    return render(request, "billing/admin/doctor_sla_dashboard.html", {
        "doctors": doctors,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def doctor_scorecard(request, doctor_id):
    """Detailed performance report for a specific doctor"""
    if not request.user.is_admin():
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    doctor = get_object_or_404(
        CustomUser,
        id=doctor_id,
        role="doctor",
        hospital=request.user.hospital
    )
    
    metrics = doctor_sla_metrics(doctor, request.user.hospital)
    grade = performance_grade(metrics["sla_compliance"], metrics["escalations"])
    
    return render(request, "billing/admin/doctor_scorecard.html", {
        "doctor": doctor,
        "metrics": metrics,
        "grade": grade,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def department_sla_dashboard(request):
    """Hospital-wide SLA compliance by department"""
    if not request.user.is_admin():
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    hospital = request.user.hospital
    departments = department_sla_metrics(hospital)
    
    return render(request, "billing/admin/department_sla_dashboard.html", {
        "departments": departments,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def doctor_sla_leaderboard(request):
    """Comparative SLA performance ranking of doctors"""
    if request.user.role not in ["admin", "doctor"]:
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    doctors = (
        VitalAlert.objects.filter(
            hospital=request.user.hospital,
            acknowledged_at__isnull=False
        )
        .values("doctor__id", "doctor__first_name", "doctor__last_name")
        .annotate(
            total=Count("id"),
            sla_met=Count("id", filter=Q(acknowledged_at__lte=F('acknowledge_deadline'))),
            breached=Count("id", filter=Q(acknowledged_at__gt=F('acknowledge_deadline'))),
            avg_response=Avg(
                ExpressionWrapper(
                    F("acknowledged_at") - F("created_at"),
                    output_field=DurationField()
                )
            )
        )
    )
    
    leaderboard = []
    for d in doctors:
        total = d["total"]
        sla_rate = round((d["sla_met"] / total) * 100, 1) if total else 0
        name = f"{d['doctor__first_name']} {d['doctor__last_name']}"
        
        leaderboard.append({
            "name": name,
            "total": total,
            "sla_rate": sla_rate,
            "breached": d["breached"],
            "avg_response": d["avg_response"],
        })
    
    leaderboard.sort(key=lambda x: (-x["sla_rate"], x["avg_response"] or timedelta(hours=999)))
    
    return render(request, "billing/doctor_sla_leaderboard.html", {
        "leaderboard": leaderboard,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def doctor_sla_self_view(request):
    """Doctor's personal SLA performance dashboard"""
    if not request.user.is_doctor():
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    alerts = VitalAlert.objects.filter(
        doctor=request.user,
        hospital=request.user.hospital
    )
    
    total = alerts.count()
    acknowledged = alerts.filter(
        acknowledged_at__isnull=False,
        acknowledged_at__lte=F("acknowledge_deadline")
    ).count()
    breached = alerts.filter(
        acknowledged_at__gt=F("acknowledge_deadline")
    ).count()
    open_alerts = alerts.filter(status="open").count()
    
    avg_response = alerts.filter(acknowledged_at__isnull=False).annotate(
        response_time=ExpressionWrapper(
            F("acknowledged_at") - F("created_at"),
            output_field=DurationField()
        )
    ).aggregate(avg=Avg("response_time"))["avg"]
    
    sla_rate = round((acknowledged / total) * 100, 1) if total else 0
    
    context = {
        "total": total,
        "acknowledged": acknowledged,
        "breached": breached,
        "open_alerts": open_alerts,
        "sla_rate": sla_rate,
        "avg_response": avg_response,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    }
    
    return render(request, "billing/doctor_sla_self.html", context)


@login_required
def doctor_sla_trend(request, doctor_id=None):
    """SLA performance trends over time"""
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    alerts = VitalAlert.objects.filter(patient__hospital=request.user.hospital)
    if doctor_id:
        alerts = alerts.filter(doctor_id=doctor_id)
    
    data = (
        alerts.annotate(month=TruncMonth("created_at"))
        .annotate(doctor_name=Concat(F('doctor__first_name'), Value(' '), F('doctor__last_name')))
        .values("doctor_name", "month")
        .annotate(
            total=Count("id"),
            sla_met=Count("id", filter=Q(acknowledged_at__lte=F('acknowledge_deadline')))
        )
        .order_by("month")
    )
    
    trends = {}
    for row in data:
        name = row.get("doctor_name") or "Unknown"
        sla_rate = round((row["sla_met"] / row["total"]) * 100, 1) if row["total"] else 0
        trends.setdefault(name, []).append({
            "month": row["month"].strftime("%b %Y"),
            "sla_rate": sla_rate,
        })
    
    # Calculate trend direction
    for months in trends.values():
        for i in range(1, len(months)):
            prev, curr = months[i-1]["sla_rate"], months[i]["sla_rate"]
            months[i]["trend"] = "up" if curr > prev else "down" if curr < prev else "flat"
        if months:
            months[0]["trend"] = "flat"
    
    return render(request, "billing/doctor_sla_trend.html", {
        "trends": trends,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def hospital_sla_settings(request):
    """Configure hospital-wide SLA policies"""
    if not request.user.is_admin():
        messages.error(request, "Unauthorized access")
        return redirect("dashboard")
    
    hospital = request.user.hospital
    form = HospitalSLAForm(request.POST or None, instance=hospital)
    
    if form.is_valid():
        form.save()
        messages.success(request, "SLA policies updated successfully")
        return redirect("hospital_sla_settings")
    
    return render(request, "billing/admin/hospital_sla.html", {
        "form": form,
        "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count(),
    })


# =======================================================
# 🧰 UTILITIES & TEMPLATES
# =======================================================

@login_required
def load_note_template(request, key):
    """AJAX endpoint to load pre-defined clinical note templates"""
    template = DOCTOR_NOTE_TEMPLATES.get(key, "")
    return JsonResponse({"template": template})
# =======================================================
# Autocomplete API Endpoint
# =======================================================

from django.http import JsonResponse

@login_required
def medicine_autocomplete(request):
    q = request.GET.get('q', '').strip()
    qs = Medicine.objects.filter(name__icontains=q)
    if hasattr(request.user, "hospital") and request.user.hospital:
        qs = qs.filter(hospital=request.user.hospital)
    results = [{"id": m.id, "name": m.name, "price": float(m.price), "qty": m.quantity} for m in qs[:10]]
    return JsonResponse(results, safe=False)



# =======================================================
# USER REGISTRATION
# =======================================================

def superadmin_login(request):
    """Login only for superadmins on root domain"""

    # Prevent access from subdomains
    if getattr(request, "hospital", None):
        return HttpResponseForbidden("Superadmin login not allowed on hospital domain.")

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            if user.role != "superadmin":
                messages.error(request, "Access denied. Superadmins only.")
                return redirect("superadmin_login")

            login(request, user)
            return redirect("superadmin_dashboard")
    else:
        form = AuthenticationForm()

    return render(request, "registration/login.html", {"hospital": None, "form": form})


def hospital_login(request, slug=None):
    hospital = getattr(request, "hospital", None)

    if hospital is None:
        return redirect("superadmin_login")

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            # 🚨 Strict isolation checks
            if user.role == "superadmin":
                return HttpResponseForbidden("Superadmins cannot log in via hospital domain.")

            if user.hospital != hospital:
                return HttpResponseForbidden("You cannot log in to this hospital.")

            login(request, user)
            return redirect("dashboard")
    else:
        form = AuthenticationForm()

    return render(request, "registration/login.html", {"hospital": hospital, "form": form})


def register(request):
    if not request.user.is_authenticated or request.user.role != "admin":
        messages.error(request, "Only hospital admins can create staff accounts.")
        return redirect("login")

    hospital = request.user.hospital

    if request.method == "POST":
        form = CustomUserCreationForm(request.POST, request_user=request.user)
        if form.is_valid():
            new_user = form.save(hospital=hospital)
            log_action(
                request.user,
                "create",
                "CustomUser",
                new_user.id,
                f"Created staff user '{new_user.username}' with role '{new_user.role}'.",
            )
            messages.success(request, f"User '{new_user.username}' created successfully.")
            return redirect("register")
    else:
        form = CustomUserCreationForm(request_user=request.user)

    staff_users = CustomUser.objects.filter(hospital=hospital).order_by("role", "username")
    return render(
        request,
        "registration/register.html",
        {
            "form": form,
            "staff_users": staff_users,
            "hospital": hospital,
        },
    )


# =======================================================
# ROLE-BASED DASHBOARDS (simple render)
# =======================================================

@login_required
def admin_dashboard(request):
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    hospital = request.user.hospital

    if request.method == "POST":
        form = CustomUserCreationForm(request.POST, request_user=request.user)
        if form.is_valid():
            new_user = form.save(hospital=hospital)
            log_action(
                request.user,
                "create",
                "CustomUser",
                new_user.id,
                f"Created staff user '{new_user.username}' with role '{new_user.role}'.",
            )
            messages.success(request, f"User '{new_user.username}' created successfully.")
            return redirect("admin_dashboard")
    else:
        form = CustomUserCreationForm(request_user=request.user)

    return render(request, "billing/dashboard_admin.html", build_admin_dashboard_context(request, form=form))


@login_required
def toggle_user_active(request, user_id):
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    staff_user = hospital_scoped_or_404(CustomUser, request.user, id=user_id)

    if request.method != "POST":
        return redirect("admin_dashboard")

    if staff_user == request.user:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("admin_dashboard")

    staff_user.is_active = not staff_user.is_active
    staff_user.save(update_fields=["is_active"])

    status_label = "activated" if staff_user.is_active else "deactivated"
    log_action(
        request.user,
        "update",
        "CustomUser",
        staff_user.id,
        f"{status_label.capitalize()} staff user '{staff_user.username}'.",
    )
    messages.success(request, f"User '{staff_user.username}' {status_label} successfully.")
    return redirect("admin_dashboard")


@login_required
def edit_staff_user(request, user_id):
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    staff_user = hospital_scoped_or_404(CustomUser, request.user, id=user_id)

    # Extra safety: hospital admins must never manage platform admins, even if someone forces it into the same hospital.
    if getattr(staff_user, "role", None) == "platform_admin" and request.user.role != "platform_admin":
        messages.error(request, "Unauthorized access.")
        return redirect("admin_dashboard")

    if request.method == "POST":
        profile_form = StaffUserUpdateForm(request.POST, instance=staff_user, request_user=request.user)
        password_form = StaffPasswordResetForm(staff_user)

        if profile_form.is_valid():
            original_values = {
                "first_name": staff_user.first_name,
                "last_name": staff_user.last_name,
                "username": staff_user.username,
                "email": staff_user.email,
                "role": staff_user.role,
                "specialty": staff_user.specialty or "",
                "is_active": staff_user.is_active,
            }
            updated_user = profile_form.save(commit=False)
            updated_user.hospital = request.user.hospital
            if updated_user == request.user and not updated_user.is_active:
                messages.error(request, "You cannot deactivate your own account.")
            else:
                updated_user.save()
                changed_fields = []
                for field_name, old_value in original_values.items():
                    new_value = getattr(updated_user, field_name) or ""
                    if old_value != new_value:
                        changed_fields.append(field_name.replace("_", " "))
                description = f"Updated staff user '{updated_user.username}'."
                if changed_fields:
                    description = (
                        f"Updated staff user '{updated_user.username}' fields: "
                        + ", ".join(changed_fields)
                        + "."
                    )
                log_action(
                    request.user,
                    "update",
                    "CustomUser",
                    updated_user.id,
                    description,
                )
                messages.success(request, f"User '{updated_user.username}' updated successfully.")
                return redirect("edit_staff_user", user_id=staff_user.id)
    else:
        profile_form = StaffUserUpdateForm(instance=staff_user, request_user=request.user)
        password_form = StaffPasswordResetForm(staff_user)

    return render(
        request,
        "billing/admin/manage_staff_user.html",
        {
            "staff_user": staff_user,
            "profile_form": profile_form,
            "password_form": password_form,
        },
    )


@login_required
def reset_staff_password(request, user_id):
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    staff_user = hospital_scoped_or_404(CustomUser, request.user, id=user_id)

    if request.method != "POST":
        return redirect("edit_staff_user", user_id=staff_user.id)

    profile_form = StaffUserUpdateForm(instance=staff_user, request_user=request.user)
    password_form = StaffPasswordResetForm(staff_user, request.POST)

    if password_form.is_valid():
        password_form.save()
        log_action(
            request.user,
            "update",
            "CustomUser",
            staff_user.id,
            f"Reset password for staff user '{staff_user.username}'.",
        )
        messages.success(request, f"Password reset successfully for '{staff_user.username}'.")
        return redirect("edit_staff_user", user_id=staff_user.id)

    return render(
        request,
        "billing/admin/manage_staff_user.html",
        {
            "staff_user": staff_user,
            "profile_form": profile_form,
            "password_form": password_form,
        },
    )


@login_required
def manage_services(request):
    """Hospital admin can manage billable services and pricing for their hospital."""
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    hospital = request.user.hospital
    unread_count = Message.objects.filter(recipient=request.user, is_read=False).count()

    if request.method == "POST":
        form = ServiceForm(request.POST)
        if form.is_valid():
            service = form.save(commit=False)
            service.hospital = hospital
            try:
                service.save()
            except IntegrityError:
                messages.error(request, "A service with this name already exists.")
            else:
                messages.success(request, f"Service '{service.name}' created.")
                return redirect("manage_services")
    else:
        form = ServiceForm()

    services = Service.objects.filter(hospital=hospital).order_by("name")
    return render(
        request,
        "billing/admin/services_manage.html",
        {"form": form, "services": services, "unread_count": unread_count},
    )


@login_required
def edit_service(request, service_id):
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    service = hospital_scoped_or_404(Service, request.user, id=service_id)
    unread_count = Message.objects.filter(recipient=request.user, is_read=False).count()

    if request.method == "POST":
        form = ServiceForm(request.POST, instance=service)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.hospital = request.user.hospital
            try:
                updated.save()
            except IntegrityError:
                messages.error(request, "A service with this name already exists.")
            else:
                messages.success(request, "Service updated.")
                return redirect("manage_services")
    else:
        form = ServiceForm(instance=service)

    return render(
        request,
        "billing/admin/service_edit.html",
        {"form": form, "service": service, "unread_count": unread_count},
    )


@login_required
def delete_service(request, service_id):
    if request.user.role != "admin":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("manage_services")

    service = hospital_scoped_or_404(Service, request.user, id=service_id)
    name = service.name
    service.delete()
    messages.success(request, f"Service '{name}' deleted.")
    return redirect("manage_services")

@login_required
def doctor_dashboard(request):
    patients = Patient.objects.filter(hospital=request.user.hospital)
    return render(request, "billing/dashboard_doctor.html", {"patients": patients})

@login_required
def receptionist_dashboard(request):
    return render(request, "billing/dashboard_receptionist.html")

@login_required
def accountant_dashboard(request):
    if request.user.role != "accountant":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    return render(
        request,
        "billing/dashboard_accountant.html",
        build_accountant_dashboard_context(request.user),
    )

@login_required
def radiologist_dashboard(request):
    return render(request, "billing/dashboard_radiologist.html")

@login_required
def lab_dashboard(request):
    return render(request, "billing/dashboard_lab.html")

@login_required
def pharmacist_dashboard(request):
    hospital = request.user.hospital
    today = timezone.localdate()
    prescriptions = (
        Prescription.objects.filter(status="issued", hospital=hospital)
        .select_related("visit__patient", "doctor")
        .order_by("-issued_at")
    )

    today_dispensed = Prescription.objects.filter(
        status="dispensed",
        hospital=hospital,
        dispensed_at__date=today,
    ).count()

    # Keep parity with the main /dashboard/ context so templates are consistent.
    unread_count = Message.objects.filter(recipient=request.user, is_read=False).count()

    context = {
        "prescriptions": prescriptions,
        "today_dispensed": today_dispensed,
        "unread_count": unread_count,
    }
    return render(request, "billing/pharmacist_dashboard.html", context)


# =======================================================
# Prescription and Dispenses
# =======================================================

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from .models import Patient, Prescription, Medicine
from .forms import PrescriptionForm

# Doctor adds a prescription
@login_required
def add_prescription(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)

    # Get current active visit
    visit = PatientVisit.objects.filter(
        patient=patient,
        hospital=request.user.hospital,
        is_active=True,
    ).exclude(status="completed").first()

    # Automatically create an active visit if none exists
    if not visit:
        reason = request.POST.get("reason") or "Prescription visit"
        visit = PatientVisit.objects.create(
            patient=patient,
            hospital=request.user.hospital,
            assigned_doctor=request.user if request.user.role == "doctor" else None,
            status="under_diagnosis",
            is_active=True,
            is_emergency=request.POST.get("is_emergency") == "on",
            reason=reason
        )

    if request.method == "POST":
        form = PrescriptionForm(request.POST)
        if form.is_valid():
            medicines = form.cleaned_data["medicines"]
            dosage = form.cleaned_data["dosage"] or "N/A"
            duration = form.cleaned_data["duration"] or "N/A"
            instructions = form.cleaned_data["instructions"] or "No special instructions"

            prescription = Prescription.objects.create(
                hospital=request.user.hospital,
                visit=visit,
                doctor=request.user,             # this is fine if Prescription model has doctor field
                medicines=medicines,
                dosage=dosage,
                duration=duration,
                instructions=instructions,
            )

            messages.success(request, "Prescription added successfully.")
            return redirect("patient_emr", patient_id=patient.id)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PrescriptionForm()

    return render(request, "billing/prescription_form.html", {"patient": patient, "form": form})

# Pharmacist sees pending prescriptions
@login_required
def pending_prescriptions(request):
    prescriptions = Prescription.objects.filter(status="issued").select_related(
        "visit__patient", "doctor", "medicine"
    )
    prescriptions = Prescription.objects.filter(
        status="issued"
    ).select_related("visit__patient", "doctor")
    return render(request, "billing/prescriptions/pending_prescriptions.html", {"prescriptions": prescriptions})


# Pharmacist marks as dispensed (reduces stock)
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from .models import Prescription

@login_required
def dispense_prescription(request, prescription_id):
    prescription = get_object_or_404(Prescription, id=prescription_id)

    # Only pharmacist can dispense
    if request.user.role != "pharmacist":
        return HttpResponse("Unauthorized", status=403)

    prescription.status = "dispensed"
    prescription.pharmacist = request.user
    prescription.dispensed_at = timezone.now()
    prescription.save()

    messages.success(request, "Prescription dispensed successfully.")
    return redirect("pharmacist_dashboard")


@login_required
def medicine_list(request):
    if request.user.role != "pharmacist":
        messages.error(request, "Only pharmacists can view this page.")
        return redirect("dashboard")

    medicines = Medicine.objects.all()
    return render(request, "billing/medicine_list.html", {"medicines": medicines})


# =======================================================
# PHARMACIST PRESCRIPTION MANAGEMENT
# =======================================================

@login_required
def pharmacist_prescriptions(request):
    # Only pharmacists should access this page
    if request.user.role != "pharmacist":
        messages.error(request, "Access denied.")
        return redirect("dashboard")

    prescriptions = Prescription.objects.filter(status="issued").select_related("visit__patient", "doctor")
    return render(request, "billing/pharmacist_prescriptions.html", {"prescriptions": prescriptions})

@login_required
def pharmacist_dispense_prescription(request, prescription_id):
    prescription = get_object_or_404(Prescription, pk=prescription_id)

    # Security check
    if request.user.role != "pharmacist":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    # POST: Pharmacist submits dispense form
    if request.method == "POST":
        notes = request.POST.get("dispensed_notes", "").strip()

        # -------------------------------
        # 1️⃣ Deduct medicine from inventory
        # -------------------------------
        lines = prescription.medicines.split("\n")  # medicine1 x 2
        errors = []

        for line in lines:
            if "x" not in line:
                continue

            med_name = line.split("x")[0].strip()
            qty_needed = int(line.split("x")[1].strip())

            try:
                med = Medicine.objects.get(
                    hospital=request.user.hospital,
                    name__iexact=med_name
                )
            except Medicine.DoesNotExist:
                errors.append(f"{med_name} is not found in inventory.")
                continue

            if med.quantity < qty_needed:
                errors.append(f"Not enough stock for: {med_name} (needed {qty_needed}, available {med.quantity})")
            else:
                # deduct from stock
                med.quantity -= qty_needed
                med.save()

        # If any errors, stop dispensing
        if errors:
            messages.error(request, "Unable to dispense prescription:")
            for e in errors:
                messages.error(request, e)
            return redirect("pharmacist_dispense_view", prescription_id=prescription.id)

        # -------------------------------
        # 2️⃣ Update prescription record
        # -------------------------------
        prescription.status = "dispensed"
        prescription.pharmacist = request.user
        prescription.dispensed_at = timezone.now()
        prescription.dispensed_notes = notes
        prescription.save()

        messages.success(request, "Prescription dispensed successfully.")
        return redirect("pharmacist_history")

    # GET request: show confirmation page
    return render(request, "billing/pharmacist_dispense_confirm.html", {
        "prescription": prescription
    })


from django.db import IntegrityError
from django.contrib import messages

@login_required
def add_medicine(request):
    if request.method == "POST":
        name = request.POST.get("name")
        price = request.POST.get("price")
        quantity = request.POST.get("quantity")

        if not name or not price or not quantity:
            messages.error(request, "All fields are required.")
            return redirect("add_medicine")

        try:
            price = float(price)
            quantity = int(quantity)
        except ValueError:
            messages.error(request, "Price and quantity must be valid numbers.")
            return redirect("add_medicine")

        try:
            Medicine.objects.create(
                hospital=request.user.hospital,
                name=name,
                price=price,
                quantity=quantity,
            )
            messages.success(request, f"{name} added successfully.")
            return redirect("medicine_list")

        except IntegrityError:
            messages.error(request, f"'{name}' already exists in your inventory.")
            return redirect("add_medicine")

    return render(request, "billing/add_medicine.html")


@login_required
def dispense_history(request):
    if request.user.role != "pharmacist":
        return HttpResponseForbidden("Not allowed.")

    history = Prescription.objects.filter(
        status="dispensed",
        pharmacist=request.user
    ).order_by("-dispensed_at")

    return render(request, "billing/dispense_history.html", {
        "history": history
    })

@login_required
def medicine_inventory(request):
    if request.user.role != "pharmacist":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    medicines = Medicine.objects.filter(hospital=request.user.hospital)

    return render(request, "medicine_inventory.html", {
        "medicines": medicines
    })

@login_required
def doctor_prescriptions(request):
    if request.user.role != "doctor":
        messages.error(request, "Unauthorized access.")
        return redirect("dashboard")

    prescriptions = Prescription.objects.filter(
        doctor=request.user
    ).order_by('-issued_at')

    
    return render(request, "billing/doctor_prescriptions.html", {
        "prescriptions": prescriptions
    })

# =======================================================
# MEDICINE MANAGEMENT VIEWS
# =======================================================

from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.contrib import messages
from .models import Medicine, StockLog
import csv
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator

@login_required
def medicine_list(request):
    hospital = request.user.hospital

    categories = MedicineCategory.objects.filter(hospital=hospital)

    q = request.GET.get("q", "")
    status = request.GET.get("status", "")
    selected_category = request.GET.get("category", "all")

    medicines = Medicine.objects.filter(hospital=hospital)

    # Search
    if q:
        medicines = medicines.filter(name__icontains=q)

    # Category
    if selected_category != "all":
        medicines = medicines.filter(category_id=selected_category)

    # Status filter
    low_stock_threshold = 10
    if status == "ok":
        medicines = medicines.filter(quantity__gt=low_stock_threshold)
    elif status == "low":
        medicines = medicines.filter(quantity__gt=0, quantity__lte=low_stock_threshold)
    elif status == "out":
        medicines = medicines.filter(quantity=0)

    paginator = Paginator(medicines.order_by("name"), 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "billing/medicine/medicine_list.html", {
        "categories": categories,
        "page_obj": page_obj,
        "selected_category": selected_category,
        "low_stock_threshold": low_stock_threshold,
    })


@login_required
def add_medicine(request):
    if not request.user.is_pharmacist() and not request.user.is_admin():
        return redirect("dashboard")

    hospital = request.user.hospital
    categories = MedicineCategory.objects.filter(hospital=hospital)

    if request.method == "POST":
        name = request.POST.get("name").strip()
        price = request.POST.get("price")
        quantity = request.POST.get("quantity")
        category_id = request.POST.get("category")
        
        category = MedicineCategory.objects.filter(id=category_id, hospital=hospital).first()

        # prevent duplicates
        if Medicine.objects.filter(hospital=hospital, name__iexact=name).exists():
            messages.error(request, "Medicine already exists.")
            return redirect("add_medicine")

        Medicine.objects.create(
            hospital=hospital,
            name=name,
            quantity=quantity,
            price=price,
            category=category,
        )

        messages.success(request, "Medicine added successfully.")
        return redirect("medicine_list")

    return render(request, "billing/medicine/add_medicine.html", {
        "categories": categories
   })



@login_required
def edit_medicine(request, pk):
    if not request.user.is_pharmacist() and not request.user.is_admin():
        return redirect("dashboard")

    hospital = request.user.hospital

    medicine = get_object_or_404(Medicine, id=pk, hospital=hospital)
    categories = MedicineCategory.objects.filter(hospital=hospital)

    if request.method == "POST":
        name = request.POST.get("name").strip()
        price = request.POST.get("price")
        quantity = request.POST.get("quantity")
        category_id = request.POST.get("category")

        # Validate category
        category = MedicineCategory.objects.filter(
            id=category_id, hospital=hospital
        ).first()

        # Prevent duplicate names on same hospital (except itself)
        if Medicine.objects.filter(
            hospital=hospital,
            name__iexact=name
        ).exclude(id=medicine.id).exists():
            messages.error(request, "A medicine with this name already exists.")
            return redirect("edit_medicine", pk=medicine.id)

        # Update safely
        medicine.name = name
        medicine.price = price
        medicine.quantity = quantity
        medicine.category = category
        medicine.save()

        messages.success(request, "Medicine updated successfully.")
        return redirect("medicine_list")

    return render(request, "billing/medicine/edit_medicine.html", {
        "medicine": medicine,
        "categories": categories,
    })


@login_required
def delete_medicine(request, pk):
    medicine = get_object_or_404(Medicine, pk=pk, hospital=request.user.hospital)

    if request.method == "POST":
        medicine.delete()
        messages.success(request, "Medicine deleted.")
        return redirect("medicine_list")

    return render(request, "billing/medicine/delete_medicine_confirmation.html", {
        "medicine": medicine
    })


@login_required
def medicine_detail(request, pk):
    medicine = get_object_or_404(Medicine, pk=pk, hospital=request.user.hospital)

    logs = StockLog.objects.filter(medicine=medicine).order_by("-timestamp")

    return render(request, "billing/medicine/medicine_details.html", {
        "medicine": medicine,
        "logs": logs
    })


@login_required
def stock_in(request, pk):
    med = get_object_or_404(Medicine, pk=pk, hospital=request.user.hospital)

    if request.method == "POST":
        qty = int(request.POST.get("quantity"))
        med.quantity += qty
        med.save()

        StockLog.objects.create(
            medicine=med,
            action="IN",
            quantity=qty,
            user=request.user
        )

        messages.success(request, f"Added {qty} units to stock.")
        return redirect("medicine_detail", pk=pk)

    return render(request, "billing/medicine/stock_form.html", {
        "medicine": med,
        "mode": "in"
    })

@login_required
def stock_out(request, pk):
    med = get_object_or_404(Medicine, pk=pk, hospital=request.user.hospital)

    if request.method == "POST":
        qty = int(request.POST.get("quantity"))

        if qty > med.quantity:
            messages.error(request, "Cannot remove more than available stock.")
            return redirect("medicine_detail", pk=pk)

        med.quantity -= qty
        med.save()

        StockLog.objects.create(
            medicine=med,
            action="OUT",
            quantity=qty,
            user=request.user
        )

        messages.success(request, f"Removed {qty} units from stock.")
        return redirect("medicine_detail", pk=pk)

    return render(request, "billing/medicine/stock_form.html", {
        "medicine": med,
        "mode": "out"
    })

#@login_required
def stock_logs_view(request):
    logs = StockLog.objects.filter(medicine__hospital=request.user.hospital)

    # Filters
    q = request.GET.get("q", "")
    med = request.GET.get("med", "")
    action = request.GET.get("action", "")

    if q:
        logs = logs.filter(
            Q(medicine__name__icontains=q) |
            Q(user__username__icontains=q)
        )

    if med:
        logs = logs.filter(medicine__id=med)

    if action in ["IN", "OUT"]:
        logs = logs.filter(action=action)

    logs = logs.order_by("-timestamp")

    medicines = Medicine.objects.filter(hospital=request.user.hospital)

    return render(request, "billing/medicine/stock_logs.html", {
        "logs": logs,
        "medicines": medicines
    })

@login_required
def inventory_dashboard(request):
    meds = Medicine.objects.filter(hospital=request.user.hospital)

    total_medicines = meds.count()
    low_stock_threshold = 10

    low_stock = meds.filter(quantity__gt=0, quantity__lte=low_stock_threshold).count()
    out_of_stock = meds.filter(quantity=0).count()
    total_quantity = meds.aggregate(total=Sum("quantity"))["total"] or 0

    recent_logs = StockLog.objects.filter(
        medicine__hospital=request.user.hospital
    ).order_by("-timestamp")[:10]

    return render(request, "billing/pharmacy/inventory_dashboard.html", {
        "total_medicines": total_medicines,
        "low_stock": low_stock,
        "out_of_stock": out_of_stock,
        "total_quantity": total_quantity,
        "recent_logs": recent_logs,
    })


# =======================================================
# EXPORT MEDICINES CSV
# =======================================================

@login_required
def export_medicines_csv(request):
    medicines = Medicine.objects.filter(hospital=request.user.hospital)

    # Apply search / filter
    q = request.GET.get("q", "")
    status = request.GET.get("status", "")
    low_threshold = 10

    if q:
        medicines = medicines.filter(name__icontains=q)

    if status == "low":
        medicines = medicines.filter(quantity__gt=0, quantity__lte=low_threshold)
    elif status == "out":
        medicines = medicines.filter(quantity=0)
    elif status == "ok":
        medicines = medicines.filter(quantity__gt=low_threshold)

    # CSV Output
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="medicine_inventory.csv"'

    writer = csv.writer(response)
    writer.writerow(["Medicine", "Price", "Quantity"])

    for med in medicines:
        writer.writerow([med.name, med.price, med.quantity])

    return response


# ------------------------------
# MEDICINE CATEGORY – ADD
# ------------------------------
@login_required
def add_category(request):

    # ---- ROLE CHECK FIX ----
    if request.user.role not in ["pharmacist", "admin"]:
        return redirect("dashboard")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()

        if not name:
            messages.error(request, "Category name cannot be empty.")
            return redirect("category_list")

        # Prevent duplicate categories for the same hospital
        if MedicineCategory.objects.filter(
            hospital=request.user.hospital,
            name__iexact=name
        ).exists():
            messages.error(request, "Category already exists.")
            return redirect("category_list")

        # Create category
        MedicineCategory.objects.create(
            hospital=request.user.hospital,
            name=name
        )

        messages.success(request, "Category added successfully.")
        return redirect("category_list")   # FIXED

    return render(request, "billing/medicine/category_add.html")  # FIXED PATH


@login_required
def category_list(request):
    categories = MedicineCategory.objects.filter(hospital=request.user.hospital)
    return render(request, "billing/medicine/category_list.html", {
        "categories": categories
    })


@login_required
def edit_category(request, category_id):
    category = get_object_or_404(
        MedicineCategory,
        id=category_id,
        hospital=request.user.hospital
    )

    # Access control: only admin & pharmacist
    if request.user.role not in ["pharmacist", "admin"]:
        return redirect("dashboard")

    if request.method == "POST":
        name = request.POST.get("name").strip()

        # Avoid duplicates
        if MedicineCategory.objects.filter(
            hospital=request.user.hospital,
            name__iexact=name
        ).exclude(id=category.id).exists():
            messages.error(request, "A category with this name already exists.")
            return redirect("category_list")

        category.name = name
        category.save()

        messages.success(request, "Category updated successfully.")
        return redirect("category_list")

    return render(request, "billing/medicine/edit_category.html", {
        "category": category
    })



@login_required
def delete_category(request, cat_id):
    category = get_object_or_404(MedicineCategory, id=cat_id, hospital=request.user.hospital)

    if request.method == "POST":
        category.delete()
        messages.success(request, "Category deleted.")
        return redirect("category_list")

    return render(request, "billing/medicine/delete_category.html", {
        "category": category
    })

# ==============================
# VITAL SIGNS
# ==============================

@login_required
def add_vital_sign(request, patient_id):
    patient = hospital_scoped_or_404(Patient, request.user, id=patient_id)
    if request.method == "POST":
        visit = PatientVisit.objects.filter(patient=patient, is_active=True).first()

        def parse_int(val):
            return int(val) if val is not None and str(val).strip() else None

        def parse_float(val):
            return float(val) if val is not None and str(val).strip() else None

        systolic = parse_int(request.POST.get("systolic"))
        diastolic = parse_int(request.POST.get("diastolic"))
        heart_rate = parse_int(request.POST.get("pulse"))
        temperature = parse_float(request.POST.get("temperature"))
        respiratory_rate = parse_int(request.POST.get("respiratory_rate"))
        spo2 = parse_int(request.POST.get("spo2"))

        vital = VitalSign.objects.create(
            patient=patient,
            visit=visit,
            blood_pressure_systolic=systolic,
            blood_pressure_diastolic=diastolic,
            heart_rate=heart_rate,
            temperature=temperature,
            respiratory_rate=respiratory_rate,
            spo2=spo2,
            recorded_by=request.user,
        )

        # Evaluate the recorded vitals
        alerts_dict = evaluate_vitals(vital)
        request.session["vital_alerts"] = list(alerts_dict.items())

        # Create an alert record if any metric is critical
        if "critical" in alerts_dict.values():
            critical_items = "; ".join(f"{k}: {v}" for k, v in alerts_dict.items() if v == "critical")
            
            sla = SLAPolicy.objects.filter(
                hospital=patient.hospital,
                severity="critical",
                active=True
            ).first()

            now = timezone.now()
            if sla:
                acknowledge_deadline = now + timedelta(minutes=sla.response_time_minutes)
                escalation_deadline = now + timedelta(minutes=sla.escalation_time_minutes)
            else:
                acknowledge_deadline = None
                escalation_deadline = None

            alert = VitalAlert.objects.create(
                patient=patient,
                vital=vital,
                doctor=visit.assigned_doctor if visit else None,
                message=("Critical vital signs detected: " + critical_items) if critical_items else "Critical vital signs detected",
                sla_policy=sla,
                acknowledge_deadline=acknowledge_deadline,
                escalation_deadline=escalation_deadline,
            )

            VitalAlertLog.objects.create(
                alert=alert,
                action="created",
                performed_by=request.user,
                notes="System detected critical vitals"
            )

        # Determine overall status
        status_priority = {"critical": 2, "high": 1, "normal": 0}
        overall_status = "normal"
        alert_messages = []

        for metric, severity in alerts_dict.items():
            if status_priority.get(severity, 0) > status_priority.get(overall_status, 0):
                overall_status = severity
            if severity != "normal":
                alert_messages.append(f"{metric.replace('_', ' ').title()}: {severity}")

        vital.status = overall_status
        vital.alert_message = "; ".join(alert_messages)
        vital.save()

        if overall_status == "critical":
            messages.error(request, "⚠️ CRITICAL vitals recorded!")
        elif overall_status == "high":
            messages.warning(request, "⚠️ Abnormal vitals detected.")
        else:
            messages.success(request, "Vitals recorded successfully.")

        return redirect("patient_emr", patient_id=patient.id)

    return render(request, "billing/add_vitals.html", {"patient": patient})



@login_required
def patient_vitals_graphs(request, patient_id):
    patient = hospital_scoped_or_404(Patient, request.user, id=patient_id)

    vitals = VitalSign.objects.filter(patient=patient).order_by("created_at")

    data = {
        "labels": [v.created_at.strftime("%d %b %H:%M") for v in vitals],
        "pulse": [v.heart_rate for v in vitals],
        "temperature": [float(v.temperature) if v.temperature else None for v in vitals],
        "systolic": [v.blood_pressure_systolic for v in vitals],
        "diastolic": [v.blood_pressure_diastolic for v in vitals],
    }

    return render(request, "billing/patient_vitals_graphs.html", {
        "patient": patient,
        "data": data,
    })



# ------------------------------------------------------------------
# NHIS CLAIMS DASHBOARD
# ------------------------------------------------------------------

@login_required
def nhis_claims_dashboard(request):
    return render(
        request,
        "billing/accountant/nhis_claims_dashboard.html",
        build_accountant_dashboard_context(request.user),
    )
