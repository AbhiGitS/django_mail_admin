# -*- coding: utf-8 -*-
from django.urls import path
from django_mail_admin import nylas_auth_views

app_name = "django_mail_admin"
urlpatterns = [
    # Nylas OAuth authentication
    path(
        "nylas/auth/<int:mailbox_id>/",
        nylas_auth_views.nylas_auth_step1,
        name="nylas_auth_start",
    ),
    path(
        "nylas/callback/",
        nylas_auth_views.nylas_auth_callback,
        name="nylas_auth_callback",
    ),
]
