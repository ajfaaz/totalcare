from messaging.models import Message
from django.utils import timezone


def unread_messages(request):
    if request.user.is_authenticated:
        return {
            "unread_count": Message.objects.filter(recipient=request.user, is_read=False).count()
        }
    return {"unread_count": 0}


def subscription_status(request):
    """Expose subscription/trial info to templates for banners and UI hints."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}

    hospital = getattr(request.user, "hospital", None)
    if not hospital:
        return {}

    sub = getattr(hospital, "subscription", None)
    if not sub:
        return {"subscription": None}

    today = timezone.localdate()
    days_left = (sub.end_date - today).days if sub.end_date else None
    return {
        "subscription": sub,
        "subscription_days_left": days_left,
        "subscription_is_expired": bool(sub.end_date and sub.end_date < today),
    }
