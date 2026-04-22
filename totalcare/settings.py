import os
from pathlib import Path
from decouple import config
from dotenv import load_dotenv
import dj_database_url

# Load environment variables for local development
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# BASE DIRECTORY


def env_bool(name, default=False):
    value = config(name, default=default)
    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off', 'release', 'prod', 'production'}:
        return False
    return bool(default)

# =====================
# SECURITY SETTINGS
# =====================
SECRET_KEY = config('SECRET_KEY', default='your-dev-secret-key')
DEBUG = env_bool('DEBUG', default=True)

DEFAULT_ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
    'totalcare.arewanetventures.com',
    '.totalcare.arewanetventures.com',
]
ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS',
    default=','.join(DEFAULT_ALLOWED_HOSTS),
    cast=lambda value: [host.strip() for host in value.split(',') if host.strip()],
)

DEFAULT_CSRF_TRUSTED_ORIGINS = [
    'http://127.0.0.1',
    'http://localhost',
    'https://*.totalcare.arewanetventures.com',
    'http://*.totalcare.arewanetventures.com',
]
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default=','.join(DEFAULT_CSRF_TRUSTED_ORIGINS),
    cast=lambda value: [origin.strip() for origin in value.split(',') if origin.strip()],
)

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
    'totalcare.middleware.SubscriptionMiddleware',
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
database_url = config('DATABASE_URL', default='')

if database_url:
    DATABASES = {
        'default': dj_database_url.parse(database_url, conn_max_age=600),
    }
elif config('DB_ENGINE', default='sqlite').lower() == 'postgres':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME'),
            'USER': config('DB_USER'),
            'PASSWORD': config('DB_PASSWORD'),
            'HOST': config('DB_HOST', default='127.0.0.1'),
            'PORT': config('DB_PORT', default='5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
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

# Paystack Payment Settings
PAYSTACK_PUBLIC_KEY = config('PAYSTACK_PUBLIC_KEY', default='pk_test_29269d6aab95e1500c090c1ff093b4dc56b6b526')
PAYSTACK_SECRET_KEY = config('PAYSTACK_SECRET_KEY', default='sk_test_6709e765c70050a94bfd2d9bd4e9ed370e8fea86')

# Allow cookies across all subdomains in production when explicitly set
SESSION_COOKIE_DOMAIN = config('SESSION_COOKIE_DOMAIN', default=None)
CSRF_COOKIE_DOMAIN = config('CSRF_COOKIE_DOMAIN', default=None)

# Only enable secure cookies in production (HTTPS)
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
