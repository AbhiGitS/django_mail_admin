import logging
import base64
import smtplib
import ssl
import threading
from typing import Optional
from urllib import parse

from django.core.mail.backends.smtp import EmailBackend
from django_mail_admin.models.outgoing import EmailAddressOAuthMapping

from .mail import create
from .models import Outbox, create_attachments
from .utils import PRIORITY

from social_django.models import UserSocialAuth
from django.core.mail.backends.base import BaseEmailBackend

from django_mail_admin.o365_utils import O365Connection
from django_mail_admin.google_utils import generate_oauth2_string

logger = logging.getLogger(__name__)


class CustomEmailBackend(EmailBackend):
    def __init__(
        self,
        host=None,
        port=None,
        username=None,
        password=None,
        use_tls=None,
        fail_silently=False,
        use_ssl=None,
        timeout=None,
        ssl_keyfile=None,
        ssl_certfile=None,
        **kwargs,
    ):
        super(CustomEmailBackend, self).__init__(fail_silently=fail_silently)
        # March2025 Note: keeping active=True in case we use this.
        # if we copy this we should add Outbox.email_host_user filtering like O365/Gmail backends
        configurations = Outbox.objects.filter(active=True)
        if len(configurations) > 1 or len(configurations) == 0:
            raise ValueError(
                "Got %(l)s active Outboxes, expected 1"
                % {"l": len(configurations)}
            )
        else:
            configuration = configurations.first()
        self.host = host or configuration.email_host
        self.port = port or configuration.email_port
        self.username = configuration.email_host_user if username is None else username
        self.password = (
            configuration.email_host_password if password is None else password
        )
        self.use_tls = configuration.email_use_tls if use_tls is None else use_tls
        self.use_ssl = configuration.email_use_ssl if use_ssl is None else use_ssl
        self.timeout = configuration.email_timeout if timeout is None else timeout
        self.ssl_keyfile = (
            configuration.email_ssl_keyfile if ssl_keyfile is None else ssl_keyfile
        )
        self.ssl_certfile = (
            configuration.email_ssl_certfile if ssl_certfile is None else ssl_certfile
        )
        self.connection = None
        self._lock = threading.RLock()


class OutboxEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        for msg in email_messages:
            try:
                email = create(
                    sender=msg.from_email,
                    recipients=msg.to,
                    cc=msg.cc,
                    bcc=msg.bcc,
                    subject=msg.subject,
                    message=msg.body,
                    headers=msg.extra_headers,
                    priority=PRIORITY.medium,
                )
                alternatives = getattr(msg, "alternatives", [])
                for content, mimetype in alternatives:
                    if mimetype == "text/html":
                        email.html_message = content
                        email.save()

                if msg.attachments:
                    attachments = create_attachments(msg.attachments)
                    email.attachments.add(*attachments)

            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception("Email queue failed")

        return len(email_messages)


class O365Backend(EmailBackend):
    """
    Backend to handle sending emails via o365 connection
    """
    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        super().__init__(fail_silently, **kwargs)
        self.conn: Optional[O365Connection] = None

        # from_email might be hydrated by us (ChargeUp) in a few scenarios:
        # 1. after class __init__ (Django will create this class so we can't add directly to __init__)
        # 2. during email sending when a new connection needs to be opened
        self.from_email: str | None = None

        self.fail_silently: bool = fail_silently
        self.configuration_id: Optional[int] = None
        self._lock = threading.RLock()

    def close(self):
        """Closes and cleans up the current connection"""
        with self._lock:
            if self.conn:
                self.conn = None
                self.from_email = None
                self.configuration_id = None
            super().close()

    def open(self):
        """Opens a new O365 connection using the relevant configuration"""
        with self._lock:
            configuration = Outbox.objects.filter(
                email_host__icontains="office365",
                email_host_user=self.from_email
            ).first()

            if not configuration:
                raise ValueError(f"Unable to find an Outbox with email_host__icontains=office365 email_host_user={self.from_email}")

            # existing saved connection is valid. no need for a new one
            if self.conn and self.configuration_id == configuration.id:
                return
            elif self.conn:
                # close the existing invalid connection
                self.close()
                self.from_email = configuration.email_host_user
                self.configuration_id = configuration.id

            # Parse O365 connection details from email_host
            parseresult = parse.urlparse(configuration.email_host)
            if not O365Connection.SCHEME == parseresult.scheme.lower():
                raise ValueError(
                    f'Invalid EMAIL_HOST scheme, expected "{O365Connection.SCHEME}", got "{parseresult.scheme}"'
                )

            # Extract connection parameters from query string
            query_dict = dict(parse.parse_qsl(parseresult.query))
            client_app_id = query_dict.get("client_app_id", "")
            client_id_key = query_dict.get("client_id_key", "")
            client_secret_key = query_dict.get("client_secret_key", "")

            try:
                # Create new connection with current configuration
                self.conn = O365Connection(
                    from_email=self.from_email,
                    client_app_id=client_app_id,
                    client_id_key=client_id_key,
                    client_secret_key=client_secret_key,
                )
            except Exception as e:
                self.close()  # Clean up on failure
                raise

    def send_messages(self, email_messages) -> int:
        """Sends email messages via O365 connection matching the from_email for each message"""
        if not email_messages:
            return 0

        with self._lock:
            sent_count = 0

            # sort by from_email to try to reduce connection closing/opening
            email_messages = sorted(email_messages, key=lambda m: m.from_email)

            for msg in email_messages:
                try:
                    # Use EmailAddressOAuthMapping to find oauth_username
                    oauth_username = (
                        EmailAddressOAuthMapping.objects.filter(send_as_email=msg.from_email)
                        .values_list('oauth_username', flat=True)
                        .first()
                    ) or msg.from_email

                    # If connection doesn't match this message's configuration, create new connection
                    if (not self.conn or self.from_email != oauth_username):
                        self.close()
                        self.from_email = oauth_username
                        # TODO: we could leverage a connection cache rather than re-init each time
                        self.open()

                    # Send the message
                    if self.conn.send_messages([msg], fail_silently=self.fail_silently):
                        sent_count += 1

                except Exception as e:
                    if not self.fail_silently:
                        raise
                    logger.error(f"Failed to send message from {msg.from_email}: {str(e)}")

            return sent_count


class GmailOAuth2Backend(EmailBackend):
    """Email backend that uses XOAUTH2 for SMTP authentication"""

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        super().__init__(fail_silently=fail_silently)
        self.connection = None
        self._lock = threading.RLock()

        # from_email might be hydrated by us (ChargeUp) in a few scenarios:
        # 1. after class __init__ (Django will create this class so we can't add directly to __init__)
        # this is currently not useful for Gmail but mirrors what's done with Outlook
        # 2. during email sending when a new connection needs to be opened
        self.from_email = None

        self.configuration_id = None
        # Initialize these to None - they'll be set when sending
        self.host = None
        self.port = None
        self.username = None
        self.password = None
        self.use_tls = None
        self.use_ssl = None
        self.timeout = None
        self.ssl_keyfile = None
        self.ssl_certfile = None

    def _initialize_connection(self, from_email: str) -> None:
        """Initialize connection settings based on from_email"""
        configuration = Outbox.objects.filter(
            email_host__icontains="gmail",
            email_host_user=from_email
        ).first()

        if not configuration:
            raise ValueError(
                f"Unable to find an Outbox with email_host__icontains=gmail email_host_user={from_email}"
            )

        self.configuration_id = configuration.id
        self.from_email = from_email

        # Initialize settings from configuration
        self.host = configuration.email_host or "smtp.gmail.com"
        self.port = configuration.email_port or "587"
        self.username = from_email  # Important: this is used for OAuth lookup
        self.password = ""  # No password needed for XOAUTH2
        self.use_tls = configuration.email_use_tls
        self.use_ssl = configuration.email_use_ssl
        self.timeout = configuration.email_timeout
        self.ssl_keyfile = configuration.email_ssl_keyfile
        self.ssl_certfile = configuration.email_ssl_certfile

    def open(self,auth_uid=None):
        """Override to use OAuth instead of password authentication"""

        try:
            # First establish the SMTP connection
            if self.use_ssl:
                self.connection = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                self.connection = smtplib.SMTP(self.host, self.port, timeout=self.timeout)

            if self.use_tls:
                self.connection.starttls()

            user_social_auth = UserSocialAuth.objects.get(
                uid=auth_uid,
                provider="google-oauth2"
            )

            creds_info = user_social_auth.extra_data
            auth_string = generate_oauth2_string(
                auth_uid,
                creds_info["access_token"],
                base64_encode=False
            )
            self.connection.docmd(
                "AUTH",
                "XOAUTH2 " + base64.b64encode(auth_string.encode("utf-8")).decode("utf-8"),
            )
            return True
        except Exception:
            logger.exception("gmail failed to open connection")
            return None

    def close(self):
        """Close the connection to the email server."""
        if self.connection is None:
            return
        try:
            try:
                self.connection.quit()
            except (ssl.SSLError, smtplib.SMTPServerDisconnected):
                self.connection.close()
            except smtplib.SMTPException:
                if self.fail_silently:
                    return
                raise
        finally:
            self.connection = None
            self.from_email = None
            self.configuration_id = None

    def send_messages(self, email_messages):
        """Send messages, reinitializing connection if from_email changes"""
        if not email_messages:
            return 0

        with self._lock:
            num_sent = 0

            # sort by from_email to reduce connection opening
            email_messages = sorted(email_messages, key=lambda m: m.from_email)

            from_email_to_oauth_cache = {}
            for message in email_messages:
                try:
                    # hack way to get the original username
                    if message.from_email in from_email_to_oauth_cache:
                        oauth_username = from_email_to_oauth_cache[message.from_email]
                    else:
                        oauth_username = (
                             EmailAddressOAuthMapping.objects.filter(send_as_email=message.from_email)
                             .values_list('oauth_username', flat=True)
                             .first()
                        ) or message.from_email
                        from_email_to_oauth_cache[message.from_email] = oauth_username

                    # Check if we need to reinitialize for a different from_email
                    if (not self.connection or
                        self.from_email != oauth_username or
                        not self.configuration_id):

                        self.close()
                        # TODO: we could leverage a connection cache rather than re-init each time
                        self._initialize_connection(oauth_username)
                        new_conn_created = self.open(auth_uid=oauth_username)

                        if not self.connection or new_conn_created is None:
                            continue

                    if self._send(message):
                        num_sent += 1

                except Exception as e:
                    if not self.fail_silently:
                        raise
                    logger.error(f"Failed to send message from {message.from_email}: {str(e)}")

            if self.connection:
                self.close()

            return num_sent
