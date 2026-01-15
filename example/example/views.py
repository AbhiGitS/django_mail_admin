"""
Example illustration of two-step auth flow for O365
"""
from django.urls import reverse
from django.shortcuts import get_object_or_404, render, redirect

from django_mail_admin.models import Mailbox


def mailbox(request):
    # Display latest recent EDI documents
    mailbox_list = Mailbox.objects.order_by("-id")[:10]
    context = {
        "mailbox_list": mailbox_list,
    }
    # Allow adding new one, by uploading a document
    return render(request, "mailbox/index.html", context)


def mailbox_auth_step1(request, id):
    # callback = absolute url to o365_auth_step_two_callback() page, https://domain.tld/steptwo
    callback = request.build_absolute_uri(reverse("mailbox_auth_step2"))
    mbx = get_object_or_404(Mailbox, pk=id)

    transport = mbx.get_connection()
    conn = transport.conn
    account = conn.account
    """
     The scopes here need to exactly match the O365 web-app provisioning in MS-Entra/Identity. Do not use the "message_all" etc helpers that O365 supports in Account.authenticate flow.
    """
    scopes = ["Mail.ReadWrite", "Mail.Send", "offline_access"]

    #'state' is a variable that is guaranteed to be sent with redirect. thus we store the mailbox object id to update the auth-infor on redirect of respective Mailbox a/c
    url, state = account.con.get_authorization_url(
        requested_scopes=scopes, redirect_uri=callback, state=id
    )

    return redirect(url)


def mailbox_auth_step2(request):
    queryprms = request.GET
    state = queryprms.get("state", None)
    mbx_id = state
    if not mbx_id:
        return render(
            request,
            "mailbox/auth.html",
            {"result": False, "message": "failed to get MBX ID!"},
        )
    mbx = get_object_or_404(Mailbox, pk=mbx_id)

    transport = mbx.get_connection()
    conn = transport.conn
    account = conn.account

    # rebuild the redirect_uri used in mailbox_auth_step1
    callback = request.build_absolute_uri(reverse("mailbox_auth_step2"))

    # get the request URL of the page which will include additional auth information
    # Example request: /steptwo?code=abc123&state=xyz456
    requested_url = request.build_absolute_uri()
    result = account.con.request_token(
        requested_url, state=state, redirect_uri=callback
    )

    # if result is True, then authentication was succesful
    #  and the auth token is stored in the token backend
    return render(request, "mailbox/auth.html", {"result": result})


# Nylas OAuth views
def mailbox_nylas_auth_step1(request, id):
    """
    Nylas OAuth Step 1: Redirect to Nylas hosted authentication
    """
    from urllib.parse import urlencode
    from django.conf import settings

    mbx = get_object_or_404(Mailbox, pk=id)

    # Build callback URL
    callback = request.build_absolute_uri(reverse("mailbox_nylas_auth_step2"))

    # Get Nylas configuration
    client_id = getattr(settings, "NYLAS_CLIENT_ID", None)
    api_uri = getattr(settings, "NYLAS_API_URI", "https://api.us.nylas.com")

    if not client_id:
        return render(
            request,
            "mailbox/auth.html",
            {"result": False, "message": "NYLAS_CLIENT_ID not configured in settings"},
        )

    # Build OAuth URL
    params = {
        "client_id": client_id,
        "redirect_uri": callback,
        "response_type": "code",
        "state": str(id),
        "access_type": "online",
    }

    auth_url = f"{api_uri}/v3/connect/auth?" + urlencode(params)
    return redirect(auth_url)


def mailbox_nylas_auth_step2(request):
    """
    Nylas OAuth Step 2: Handle callback and exchange code for grant
    """
    from django.conf import settings

    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")

    if error:
        return render(
            request,
            "mailbox/auth.html",
            {"result": False, "message": f"Nylas OAuth error: {error}"},
        )

    if not code or not state:
        return render(
            request,
            "mailbox/auth.html",
            {"result": False, "message": "Missing code or state parameter"},
        )

    mbx = get_object_or_404(Mailbox, pk=state)

    try:
        from nylas import Client

        api_key = getattr(settings, "NYLAS_API_KEY", None)
        api_uri = getattr(settings, "NYLAS_API_URI", "https://api.us.nylas.com")
        client_id = getattr(settings, "NYLAS_CLIENT_ID", None)
        client_secret = getattr(settings, "NYLAS_CLIENT_SECRET", None)

        if not all([api_key, client_id, client_secret]):
            return render(
                request,
                "mailbox/auth.html",
                {
                    "result": False,
                    "message": "Nylas configuration incomplete in settings",
                },
            )

        client = Client(api_key=api_key, api_uri=api_uri)

        # Exchange code for grant
        callback = request.build_absolute_uri(reverse("mailbox_nylas_auth_step2"))
        exchange_request = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": callback,
        }

        grant_response = client.auth.exchange_code_for_token(exchange_request)
        grant_data = (
            grant_response.data if hasattr(grant_response, "data") else grant_response
        )

        grant_id = getattr(grant_data, "grant_id", None) or grant_data.get("grant_id")
        email = getattr(grant_data, "email", None) or grant_data.get("email")
        provider = getattr(grant_data, "provider", None) or grant_data.get("provider")

        if not grant_id:
            return render(
                request,
                "mailbox/auth.html",
                {"result": False, "message": "Failed to obtain grant_id from Nylas"},
            )

        # Update mailbox with grant using helper method
        metadata = {}
        if hasattr(grant_data, "__dict__"):
            metadata = {
                k: v
                for k, v in grant_data.__dict__.items()
                if k not in ["grant_id", "email", "provider", "grant_status"]
            }

        mbx.update_nylas_grant_id(
            grant_id=grant_id,
            email=email or mbx.from_email or "unknown@email.com",
            provider=provider or "unknown",
            metadata=metadata,
        )

        return render(
            request,
            "mailbox/auth.html",
            {
                "result": True,
                "message": f"Successfully connected Nylas account: {email} ({provider})",
            },
        )

    except Exception as e:
        return render(
            request,
            "mailbox/auth.html",
            {"result": False, "message": f"Nylas authentication failed: {str(e)}"},
        )
