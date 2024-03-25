import logging
import threading

from urllib import parse

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend

from .mail import create
from .models import Outbox, create_attachments
from .utils import PRIORITY

from django_mail_admin.o365_utils import O365Connection

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


class O365Backend(BaseEmailBackend):
    """
    Backend to handle sending emails via o365 connection
    """

    scheme = O365Connection.scheme  # "office365"

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        super().__init__(fail_silently, **kwargs)
        self.conn: O365Connection = None
        self.from_email: str = None
        self.fail_silently: bool = fail_silently
        self._lock = threading.RLock()

    def open(self):
        configuration: Outbox = None

        configurations = Outbox.objects.filter(active=True)
        if len(configurations) > 1 or len(configurations) == 0:
            raise ValueError(
                "Got %(l)s active configurations, expected 1"
                % {"l": len(configurations)}
            )
        else:
            configuration = configurations.first()

        self.from_email = configuration.email_host_user

        self.connection = None
        parseresult = parse.urlparse(configuration.email_host)
        if not self.scheme == parseresult.scheme.lower():
            raise ValueError(
                f'Invalid EMAIL_HOST scheme, expected "{self.scheme}", got "{parseresult.scheme}"'
            )
        query_dict = dict(parse.parse_qsl(parseresult.query))
        client_id_key = query_dict.get("client_id_key", "")
        client_secret_key = query_dict.get("client_secret_key", "")

        self.conn = O365Connection(
            from_email=self.from_email,
            client_id_key=client_id_key,
            client_secret_key=client_secret_key,
        )

    def send_messages(self, email_messages) -> int:
        if not self.conn or not self.from_email:
            raise Exception(f"Backend not yet ready to send_messages")
        return self.conn.send_messages(email_messages, fail_silently=self.fail_silently)
