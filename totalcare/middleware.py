# totalcare/middleware.py
from django.http import Http404
from django.core.cache import cache
from billing.models import Hospital
from django.contrib.auth import logout
from django.http import HttpResponseForbidden

class HospitalMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0]
        request.hospital = None

        # Allow local development
        if host in ['localhost', '127.0.0.1', 'testserver']:
            return self.get_response(request)

        parts = host.split('.')
        
        # Handle root domain (optional)
        if len(parts) < 3 or host == "totalcare.arewanetventures.com":
            # For root domain, don't set hospital
            return self.get_response(request)

        subdomain = parts[0]
        if subdomain in ["www", "admin", "mail"]:
            raise Http404("Reserved subdomain.")

        cache_key = f"hospital_{subdomain}"
        hospital = cache.get(cache_key)

        if hospital is None:
            try:
                hospital = Hospital.objects.get(slug=subdomain)
                cache.set(cache_key, hospital, 60 * 10)
            except Hospital.DoesNotExist:
                raise Http404("Hospital not found.")

        request.hospital = hospital
        return self.get_response(request)


class EnforceHospitalIsolationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            hospital = getattr(request, 'hospital', None)

            if hospital:
                # On hospital subdomain - enforce user belongs to this hospital
                if hasattr(request.user, 'hospital') and request.user.hospital != hospital:
                    logout(request)
                    return HttpResponseForbidden("You don't belong to this hospital.")
            else:
                # On root domain - only allow super users (if you had them)
                # Since you don't have superadmin role, allow all authenticated users
                pass

        return self.get_response(request)


class SubscriptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # URLs that don't require subscription check
        exempt_urls = [
            '/platform/payment/',
            '/verify-payment/',
            '/payment-failed/',
            '/demo/',
            '/',  # home page
            '/login/',
            '/logout/',
            '/admin/',
        ]
        
        # Check if current path starts with any exempt URL
        for exempt_url in exempt_urls:
            if request.path.startswith(exempt_url):
                return self.get_response(request)
        
        if (request.user.is_authenticated and 
            hasattr(request.user, 'hospital') and 
            request.user.hospital and
            request.user.role != 'platform_admin'):  # Platform admins don't need subscription
            
            if not request.user.hospital.is_subscription_active:
                from django.shortcuts import redirect
                return redirect('payment_page')

        return self.get_response(request)
