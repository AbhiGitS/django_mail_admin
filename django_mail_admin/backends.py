import logging
import base64
import threading
from typing import Optional
from urllib import parse

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend

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
        # TODO: implement choosing backend for a letter as a param
        configurations = Outbox.objects.filter(active=True)
        if len(configurations) > 1 or len(configurations) == 0:
            raise ValueError(
                "Got %(l)s active configurations, expected 1"
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
        self.from_email: Optional[str] = None
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

    def _validate_configuration(self, configuration: Outbox) -> None:
        """Validates if we need to create a new connection based on configuration"""
        if not configuration:
            raise ValueError("No active email configuration found")
        
        # If configuration has changed, close existing connection
        if self.configuration_id != configuration.id:
            self.close()
            
        # If connection exists, validate it's still using correct email
        if self.conn and self.from_email != configuration.email_host_user:
            self.close()

    def open(self):
        """Opens a new O365 connection using the active configuration"""
        with self._lock:
            # Get active configuration
            configuration = Outbox.objects.filter(active=True).first()
            
            # Validate configuration and connection state
            self._validate_configuration(configuration)
            
            # If we already have a valid connection, return
            if self.conn and self.from_email:
                return
            
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
        """Sends email messages via O365 connection matching the from_email"""
        if not email_messages:
            return 0
            
        with self._lock:
            sent_count = 0
            
            for msg in email_messages:
                try:
                    # Try to get configuration matching this message's from_email
                    configuration = Outbox.objects.filter(
                        active=True, 
                        email_host_user=msg.from_email
                    ).first()
                    
                    if not configuration:
                        raise ValueError(
                            f"No active configuration found for sender: {msg.from_email}"
                        )

                    # If connection doesn't match this message's configuration, create new connection
                    if (not self.conn or 
                        self.from_email != msg.from_email or 
                        self.configuration_id != configuration.id):
                        self.close()
                        self.from_email = msg.from_email
                        self.open()

                    # Send the message
                    if self.conn.send_messages([msg], fail_silently=self.fail_silently):
                        sent_count += 1

                except Exception as e:
                    if not self.fail_silently:
                        raise
                    logger.error(f"Failed to send message from {msg.from_email}: {str(e)}")
                    
            return sent_count


class GmailOAuth2Backend(CustomEmailBackend):
    """Override CustomEmailBackend to perform XOAUTH2 for SMTP"""

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        configuration: Outbox = None
        configurations = Outbox.objects.filter(active=True)
        if len(configurations) > 1 or len(configurations) == 0:
            raise ValueError(
                "Got %(l)s active configurations, expected 1"
                % {"l": len(configurations)}
            )
        else:
            configuration = configurations.first()

        super(GmailOAuth2Backend, self).__init__(
            host=configuration.email_host
            if configuration.email_host
            else "smtp.gmail.com",  # TODO read default from env/config
            port=configuration.email_port
            if configuration.email_port
            else "587",  # TODO read default from env/config
            username=configuration.email_host_user,  # must be an google powered email address.
            password="",  # no pwd; just XOAUTH2
            use_tls=configuration.email_use_tls,
            fail_silently=False,
            use_ssl=configuration.email_use_ssl,
            timeout=configuration.email_timeout,
            ssl_keyfile=configuration.email_ssl_keyfile,
            ssl_certfile=configuration.email_ssl_certfile,
        )

    def open(self):
        """override this to refresh token and OAUTH"""
        retval = super(GmailOAuth2Backend, self).open()
        if self.connection and retval:
            # Retrieve the user's social auth credentials
            user_social_auth = UserSocialAuth.objects.get(
                uid=self.username, provider="google-oauth2"
            )
            creds_info = user_social_auth.extra_data
            auth_string = generate_oauth2_string(
                self.username, creds_info["access_token"], base64_encode=False
            )
            self.connection.docmd(
                "AUTH",
                "XOAUTH2 "
                + base64.b64encode(auth_string.encode("utf-8")).decode("utf-8"),
            )
            return True
        return False
