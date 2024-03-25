# -*- coding: utf-8
from __future__ import unicode_literals, absolute_import

import django
import os

from decouple import config

DEBUG = True
USE_TZ = True

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(os.path.dirname(__file__), "test.db"),
        "TEST_NAME": os.path.join(os.path.dirname(__file__), "test.db"),
    }
}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "TIMEOUT": 36000,
        "KEY_PREFIX": "django_mail_admin",
    },
    "django_mail_admin": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "TIMEOUT": 36000,
        "KEY_PREFIX": "django_mail_admin",
    },
}

DJANGO_MAIL_ADMIN = {
    "BACKENDS": {
        "default": "django.core.mail.backends.dummy.EmailBackend",
        "locmem": "django.core.mail.backends.locmem.EmailBackend",
        "error": "tests.test_backends.ErrorRaisingBackend",
        "smtp": "django.core.mail.backends.smtp.EmailBackend",
        "connection_tester": "django_mail_admin.tests.test_mail.ConnectionTestingBackend",
        "custom": "django_mail_admin.backends.CustomEmailBackend",
        "o365": "django_mail_admin.backends.O365Backend",
    }
}

O365_ADMIN_SETTINGS = {
    "TOKEN_BACKEND": "FileSystemTokenBackend",
    "O365_CLIENT_ID": config("O365_CLIENT_ID"),
    "O365_CLIENT_SECRET": config("O365_CLIENT_SECRET"),
}
O365_WEBAPP_SETTINGS = {
    "test_webapp1": {
        # TBD certificate support
        "auth": "token_secret",  #'certificate'
        "O365_CLIENT_ID": config("O365_CLIENT_ID"),
        "O365_CLIENT_SECRET": config("O365_CLIENT_SECRET"),
    },
}

O365_TOKEN_BACKENDS = {
    "FileSystemTokenBackend": {
        "O365_AUTH_BACKEND_TOKEN_DIR": config("O365_AUTH_BACKEND_TOKEN_DIR"),
        "O365_AUTH_BACKEND_TOKEN_FILE": config("O365_AUTH_BACKEND_TOKEN_FILE"),
    },
}

O365_MAILBOXES = {
    "TestUser1": {"email": "test_user@test.com", "webapp": "test_webapp1"},
}

O365_TEST_ACCOUNT = config("O365_TEST_ACCOUNT")

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(BASE_DIR, "templates"),
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ROOT_URLCONF = "tests.urls"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sites",
    "django_mail_admin",
]

SITE_ID = 1

if django.VERSION >= (1, 10):
    MIDDLEWARE = ()
else:
    MIDDLEWARE_CLASSES = ()
