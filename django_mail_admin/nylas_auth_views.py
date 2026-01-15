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

    # Exchange code for grant
    try:
        from nylas import Client

        api_key = getattr(settings, "NYLAS_API_KEY", None)
        api_uri = getattr(settings, "NYLAS_API_URI", "https://api.us.nylas.com")
        client_id = getattr(settings, "NYLAS_CLIENT_ID", None)
        client_secret = getattr(settings, "NYLAS_CLIENT_SECRET", None)

        if not all([api_key, client_id, client_secret]):
            return render(
                request,
                "django_mail_admin/nylas_auth.html",
                {
                    "result": False,
                    "message": "Nylas configuration incomplete. Check NYLAS_API_KEY, NYLAS_CLIENT_ID, and NYLAS_CLIENT_SECRET in settings.",
                },
            )

        client = Client(api_key=api_key, api_uri=api_uri)

        # Exchange authorization code for grant
        callback_url = request.build_absolute_uri(
            reverse("django_mail_admin:nylas_auth_callback")
        )

        exchange_request = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": callback_url,
        }

        logger.info(f"Exchanging code for grant for mailbox {mailbox.id}")
        grant_response = client.auth.exchange_code_for_token(exchange_request)

        # Extract grant information
        grant_data = (
            grant_response.data if hasattr(grant_response, "data") else grant_response
        )
        grant_id = getattr(grant_data, "grant_id", None) or grant_data.get("grant_id")
        email = getattr(grant_data, "email", None) or grant_data.get("email")
        provider = getattr(grant_data, "provider", None) or grant_data.get("provider")

        if not grant_id:
            return render(
                request,
                "django_mail_admin/nylas_auth.html",
                {
                    "result": False,
                    "message": "Failed to obtain grant_id from Nylas response",
                },
            )

        # Update BOTH model and URI using the helper method
        metadata = {}
        if hasattr(grant_data, "__dict__"):
            metadata = {
                k: v
                for k, v in grant_data.__dict__.items()
                if k not in ["grant_id", "email", "provider", "grant_status"]
            }

        mailbox.update_nylas_grant_id(
            grant_id=grant_id,
            email=email or mailbox.from_email or "unknown@email.com",
            provider=provider or "unknown",
            metadata=metadata,
        )

        logger.info(
            f"Successfully authenticated mailbox {mailbox.id} with grant {grant_id}"
        )

        return render(
            request,
            "django_mail_admin/nylas_auth.html",
            {
                "result": True,
                "mailbox": mailbox,
                "email": email,
                "provider": provider,
                "grant_id": grant_id,
            },
        )

    except Exception as e:
        logger.error(
            f"Nylas grant exchange failed for mailbox {mailbox.id}: {e}", exc_info=True
        )
        return render(
            request,
            "django_mail_admin/nylas_auth.html",
            {"result": False, "message": f"Grant exchange failed: {str(e)}"},
        )
