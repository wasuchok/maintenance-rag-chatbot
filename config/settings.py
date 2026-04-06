import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def get_list_env(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return list(default or [])

    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_URL = os.getenv("OLLAMA_CHAT_URL", f"{OLLAMA_BASE_URL}/api/chat")
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", f"{OLLAMA_BASE_URL}/api/embed")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text-v2-moe")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.0"))
RAG_ONLY_MODE = get_bool_env("RAG_ONLY_MODE", False)
RAG_INCLUDE_CHAT_HISTORY = get_bool_env("RAG_INCLUDE_CHAT_HISTORY", True)
RAG_SEARCH_TOP_K = max(1, int(os.getenv("RAG_SEARCH_TOP_K", "8")))
SQLSERVER_HOST = os.getenv("SQLSERVER_HOST", "").strip()
SQLSERVER_PORT = get_int_env("SQLSERVER_PORT", 1433)
SQLSERVER_DATABASE = os.getenv("SQLSERVER_DATABASE", "").strip()
SQLSERVER_USERNAME = os.getenv("SQLSERVER_USERNAME", "").strip()
SQLSERVER_PASSWORD = os.getenv("SQLSERVER_PASSWORD", "")
SQLSERVER_DRIVER = os.getenv("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server").strip()
SQLSERVER_CLIENT = os.getenv("SQLSERVER_CLIENT", "pytds").strip().lower() or "pytds"
SQLSERVER_ENCRYPT = get_bool_env("SQLSERVER_ENCRYPT", False)
SQLSERVER_TRUST_SERVER_CERTIFICATE = get_bool_env(
    "SQLSERVER_TRUST_SERVER_CERTIFICATE",
    True,
)
SQLSERVER_TRUSTED_CONNECTION = get_bool_env("SQLSERVER_TRUSTED_CONNECTION", False)
SQLSERVER_CONNECTION_TIMEOUT = max(1, get_int_env("SQLSERVER_CONNECTION_TIMEOUT", 30))
SQLSERVER_CASES_SCHEMA = os.getenv("SQLSERVER_CASES_SCHEMA", "dbo").strip() or "dbo"
SQLSERVER_CASES_TABLE = os.getenv("SQLSERVER_CASES_TABLE", "").strip()
SQLSERVER_JOB_CARD_SCHEMA = os.getenv("SQLSERVER_JOB_CARD_SCHEMA", "dbo").strip() or "dbo"
SQLSERVER_JOB_CARD_VIEW = os.getenv("SQLSERVER_JOB_CARD_VIEW", "v_MT_JOB_CARD").strip()
SQLSERVER_JOB_CARD_SYNC_OVERLAP_MINUTES = max(
    0,
    get_int_env("SQLSERVER_JOB_CARD_SYNC_OVERLAP_MINUTES", 60),
)
IMPORT_API_KEY = os.getenv("IMPORT_API_KEY", "").strip()
CORS_ALLOW_ALL_ORIGINS = get_bool_env("CORS_ALLOW_ALL_ORIGINS", True)
CORS_ALLOWED_ORIGINS = get_list_env("CORS_ALLOWED_ORIGINS", [])
CORS_ALLOW_CREDENTIALS = get_bool_env("CORS_ALLOW_CREDENTIALS", False)
CORS_ALLOW_METHODS = get_list_env(
    "CORS_ALLOW_METHODS",
    ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
CORS_ALLOW_HEADERS = get_list_env(
    "CORS_ALLOW_HEADERS",
    [
        "Accept",
        "Accept-Language",
        "Authorization",
        "Content-Language",
        "Content-Type",
        "Origin",
        "X-Requested-With",
    ],
)
CORS_EXPOSE_HEADERS = get_list_env("CORS_EXPOSE_HEADERS", [])
CORS_PREFLIGHT_MAX_AGE = max(0, get_int_env("CORS_PREFLIGHT_MAX_AGE", 86400))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-#m__kq6n-7sfq47vc1w^8a@2=-r901s_lqycfp6vldwd4la@--'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'chatbot',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'chatbot.middleware.SimpleCORSMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

ALLOWED_HOSTS = ["*"]
