import os
from pathlib import Path
from decouple import config
from dotenv import load_dotenv

# Load environment variables for local development
load_dotenv()

# BASE DIRECTORY
BASE_DIR = Path(__file__).resolve().parent.parent

# =====================
# SECURITY SETTINGS
# =====================
SECRET_KEY = config('SECRET_KEY', default='your-dev-secret-key')
DEBUG = config('DEBUG', default=True, cast=bool)

# Add all allowed domains
ALLOWED_HOSTS = [
    "totalcare.arewanetventures.com",
    ".totalcare.arewanetventures.com",  # Accepts all subdomains
]

CSRF_TRUSTED_ORIGINS = [
    "https://*.totalcare.arewanetventures.com",
    "http://*.totalcare.arewanetventures.com",  # Add this for HTTP testing
]

# =====================
# APPLICATIONS
# =====================
INSTALLED_APPS = [
    'billing',
    'messaging',
    'channels',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',  # For local dev
    'django.contrib.staticfiles',
]

# =====================
# MIDDLEWARE
# =====================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'totalcare.middleware.HospitalMiddleware',
    'totalcare.middleware.EnforceHospitalIsolationMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# =====================
# URL AND WSGI/ASGI
# =====================
ROOT_URLCONF = 'totalcare.urls'
WSGI_APPLICATION = 'totalcare.wsgi.application'
ASGI_APPLICATION = 'totalcare.asgi.application'

# =====================
# CHANNELS
# =====================
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

# =====================
# TEMPLATES
# =====================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'billing.context_processors.unread_messages',
            ],
        },
    },
]

# =====================
# DATABASE
# =====================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'jvlbvywb_totalcare_db',
        'USER': 'jvlbvywb_Totalcare',
        'PASSWORD': '-8v2a;=pWsaaTT*u',
        'HOST': '127.0.0.1',
        'PORT': '5432',
    }
}

# =====================
# PASSWORD VALIDATION
# =====================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =====================
# LOCALIZATION
# =====================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# =====================
# STATIC FILES
# =====================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # Where collectstatic collects files
STATICFILES_DIRS = [BASE_DIR / 'static']  # Where Django looks for static files during development
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

# =====================
# AUTH
# =====================
AUTH_USER_MODEL = 'billing.CustomUser'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# =====================
# EMAIL
# =====================
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='your_email@gmail.com')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='your_app_password')

# =====================
# DEFAULT AUTO FIELD
# =====================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =====================
# LOGGING
# =====================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
}

# =====================
# CUSTOM SETTINGS
# =====================
VITAL_ALERT_ESCALATION_RULES = {
    1: {"minutes": 10, "role": "head_doctor"},
    2: {"minutes": 20, "role": "admin"},
}

# =====================
# CSR Trust
# =====================
CSRF_TRUSTED_ORIGINS = [
    "https://*.totalcare.arewanetventures.com",
    "http://*.totalcare.arewanetventures.com",
]


# Allow cookies across all subdomains
SESSION_COOKIE_DOMAIN = ".totalcare.arewanetventures.com"
CSRF_COOKIE_DOMAIN = ".totalcare.arewanetventures.com"

# Only enable secure cookies in production (HTTPS)
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True