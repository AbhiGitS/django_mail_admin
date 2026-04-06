"""
Nylas OAuth authentication views for web-based grant creation
"""
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from django_mail_admin.models import Mailbox
from django_mail_admin.exceptions import NylasException

logger = logging.getLogger(__name__)


def nylas_auth_step1(request, mailbox_id):
    """
    Step 1: Initiate Nylas OAuth flow

    Redirects the user to Nylas hosted authentication page.
    The state parameter contains the mailbox_id to track which mailbox
    is being authenticated.

    Args:
        request: Django HTTP request
        mailbox_id: ID of the Mailbox to authenticate

    Returns:
        HttpResponse: Redirect to Nylas OAuth page
    """
    mailbox = get_object_or_404(Mailbox, pk=mailbox_id)

    # Build callback URL
    callback_url = request.build_absolute_uri(
        reverse("django_mail_admin:nylas_auth_callback")
    )

    # Get Nylas OAuth configuration from settings
    client_id = getattr(settings, "NYLAS_CLIENT_ID", None)
    if not client_id:
        return render(
            request,
            "django_mail_admin/nylas_auth.html",
            {
                "result": False,
                "message": "NYLAS_CLIENT_ID not configured in Django settings",
            },
        )

    api_uri = getattr(settings, "NYLAS_API_URI", "https://api.us.nylas.com")

    # Nylas OAuth parameters
    params = {
        "client_id": client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "state": str(mailbox_id),  # Track which mailbox
        "access_type": "online",
    }

    # Optionally specify provider (google, microsoft, etc.)
    provider = request.GET.get("provider")
    if provider:
        params["provider"] = provider

    # Build authorization URL
    auth_url = f"{api_uri}/v3/connect/auth?" + urlencode(params)

    logger.info(f"Initiating Nylas OAuth for mailbox {mailbox_id} ({mailbox.name})")
    return redirect(auth_url)


def nylas_auth_callback(request):
    """
    Step 2: Handle Nylas OAuth callback

    Exchanges the authorization code for a grant and updates the mailbox
    with the grant_id in both the NylasGrant model and URI.

    Args:
        request: Django HTTP request with code and state parameters

    Returns:
        HttpResponse: Rendered template showing success/failure
    """
    code = request.GET.get("code")
    state = request.GET.get("state")  # mailbox_id
    error = request.GET.get("error")

    if error:
        logger.error(f"Nylas OAuth error: {error}")
        return render(
            request,
            "django_mail_admin/nylas_auth.html",
            {"result": False, "message": f"Authentication failed: {error}"},
        )

    if not code or not state:
        return render(
            request,
            "django_mail_admin/nylas_auth.html",
            {
                "result": False,
                "message": "Missing authorization code or state parameter",
            },
        )

    mailbox = get_object_or_404(Mailbox, pk=state)

    # Exchange code for grant using shared utility function
    from django_mail_admin.nylas_utils import exchange_nylas_code_for_grant

    callback_url = request.build_absolute_uri(
        reverse("django_mail_admin:nylas_auth_callback")
    )

    success, message, grant_info = exchange_nylas_code_for_grant(
        code=code, redirect_uri=callback_url, mailbox=mailbox
    )

    if success and grant_info:
        return render(
            request,
            "django_mail_admin/nylas_auth.html",
            {
                "result": True,
                "mailbox": grant_info["mailbox"],
                "email": grant_info["email"],
                "provider": grant_info["provider"],
                "grant_id": grant_info["grant_id"],
            },
        )
    else:
        return render(
            request,
            "django_mail_admin/nylas_auth.html",
            {"result": False, "message": message},
        )
