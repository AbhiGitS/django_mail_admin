import gzip
import logging
import mimetypes
import os.path
import uuid
from email.message import Message as EmailMessage
from io import BytesIO
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, unquote, urlparse
import dateparser
from dateutil import parser

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile, File
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from django_mail_admin import utils
from django_mail_admin.settings import (
    get_allowed_mimetypes,
    strip_unallowed_mimetypes,
    get_altered_message_header,
    get_text_stored_mimetypes,
    get_store_original_message,
    get_compress_original_message,
    get_attachment_interpolation_header,
)
from django_mail_admin.signals import message_received
from django_mail_admin.transports import (
    Pop3Transport,
    ImapTransport,
    MaildirTransport,
    MboxTransport,
    BabylTransport,
    MHTransport,
    MMDFTransport,
    GmailImapTransport,
    O365Transport,
)

logger = logging.getLogger(__name__)


class Outbox(models.Model):
    name = models.CharField(_("Name"), max_length=255)
    email_use_tls = models.BooleanField("EMAIL_USE_TLS", default=True)
    email_use_ssl = models.BooleanField("EMAIL_USE_SSL", default=False)
    email_ssl_keyfile = models.CharField(
        "EMAIL_SSL_KEYFILE", max_length=1024, null=True, blank=True
    )
    email_ssl_certfile = models.CharField(
        "EMAIL_SSL_CERTFILE", max_length=1024, null=True, blank=True
    )
    email_host = models.CharField("EMAIL_HOST", max_length=1024)
    email_host_user = models.CharField("EMAIL_HOST_USER", max_length=255)
    email_host_password = models.CharField("EMAIL_HOST_PASSWORD", max_length=255)
    email_port = models.PositiveSmallIntegerField("EMAIL_PORT", default=587)
    email_timeout = models.PositiveSmallIntegerField(
        "EMAIL_TIMEOUT", null=True, blank=True
    )
    # deprecated field we're no longer actively using. keeping for test/code compatability.
    active = models.BooleanField(_("Active"), default=False)

    def test_connection(self):
        """
        Test the connection to this outbox using configured credentials.

        Returns:
            tuple: (success, message) where success is a boolean indicating if
                   the connection was successful, and message contains details
                   about the connection attempt.
        """
        from django_mail_admin.connections import connections

        try:
            # Create a backend alias based on the email host type
            if "office365" in self.email_host.lower():
                backend_alias = "o365;;;" + self.email_host_user
            elif "gmail" in self.email_host.lower():
                backend_alias = "gmail;;;" + self.email_host_user
            else:
                backend_alias = "smtp;;;" + self.email_host_user

            # Get a connection using the ConnectionHandler
            connection = connections[backend_alias]

            # Test the connection - this will vary by backend type
            if hasattr(connection, "connection") and connection.connection:
                # For SMTP-based backends, we can use the noop() method
                if hasattr(connection.connection, "noop"):
                    connection.connection.noop()

                # For O365Backend, check if authenticated
                if hasattr(connection, "conn") and connection.conn:
                    if (
                        hasattr(connection.conn, "is_authenticated")
                        and not connection.conn.is_authenticated
                    ):
                        return False, "Office365 connection not authenticated"

            return True, f"Successfully connected to {self.email_host}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
        finally:
            # Close the connection to clean up
            if "connection" in locals() and connection:
                connection.close()

    def save(self, *args, **kwargs):
        # Only one item can be active at a time

        # March2025 Note: this was a previous side effect on Outbox.save
        # we're not using the concept of a singleton Active Outbox

        # if self.active:
        #     # select all other active items
        #     qs = type(self).objects.filter(active=True)
        #     # except self (if self already exists)
        #     if self.pk:
        #         qs = qs.exclude(pk=self.pk)
        #     # and deactive them
        #     qs.update(active=False)

        super(Outbox, self).save(*args, **kwargs)

    def clean(self):
        if self.email_use_ssl and self.email_use_tls:
            raise ValidationError(
                _(
                    "EMAIL_USE_TLS/EMAIL_USE_SSL are mutually exclusive, so only set one of those settings to True."
                )
            )

    def __str__(self):
        return "%(email_host_user)s@%(email_host)s:%(email_port)s" % {
            "email_host_user": self.email_host_user,
            "email_host": self.email_host,
            "email_port": self.email_port,
        }

    class Meta:
        verbose_name = _("Outbox")
        verbose_name_plural = _("Outboxes")


class ActiveMailboxManager(models.Manager):
    def get_queryset(self):
        return (
            super(ActiveMailboxManager, self)
            .get_queryset()
            .filter(
                active=True,
            )
        )


class Mailbox(models.Model):
    name = models.CharField(
        _("Name"),
        max_length=255,
    )

    uri = models.CharField(
        _("URI"),
        max_length=255,
        help_text=(
            _(
                "Example: imap+ssl://myusername:mypassword@someserver <br />"
                "<br />"
                "Internet transports include 'imap' and 'pop3'; "
                "common local file transports include 'maildir', 'mbox', "
                "and less commonly 'babyl', 'mh', and 'mmdf'. <br />"
                "<br /><br />"
                "For Office 365 email accounts use: 'office365:username@example.com:/?"
                "client_id_key=<client_id_key>&client_secret_key=<client_secret_key>&client_app_id=<client_app_id_str>'. Default values of client_id_key and client_secret_key are 'O365_CLIENT_ID', and 'O365_CLIENT_SECRET'. When all 3 are provided it will lookup client_id_key/secret_key values from client_app_id configuration."
                "<br />supports only on-behalf-of-a-user; thus requires user's auth & consent in a separate authentication flow via console/ or web-browser."
                "<br />"
                "Be sure to urlencode your username and password should they "
                "contain illegal characters (like @, :, etc)."
                "<br />"
            )
        ),
        blank=True,
        null=True,
        default=None,
    )

    def test_connection(self):
        """
        Test the connection to this mailbox using configured credentials.

        Returns:
            tuple: (success, message) where success is a boolean indicating if
                   the connection was successful, and message contains details
                   about the connection attempt.
        """
        try:
            connection = self.get_connection()
            if not connection:
                return False, "Could not establish connection - invalid configuration"

            # Test the connection based on the transport type
            if self.type == "imap" or self.type == "gmail":
                # IMAP connections have a noop() method to test the connection
                connection.server.noop()
            elif self.type == "pop3":
                # POP3 connections have a noop() method
                connection.server.noop()
            elif self.type == O365Transport.SCHEME:  # 'office365'
                # For Office365, we can check if the connection is authenticated
                if not connection.is_authenticated:
                    return False, "Office365 connection not authenticated"
            # For local file transports, just check if the connection exists
            elif self.type in ["maildir", "mbox", "babyl", "mh", "mmdf"]:
                # These are local file transports, so just check if the path exists
                if not connection:
                    return False, f"Could not access local transport at {self.location}"

            return True, f"Successfully connected to {self.name}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    from_email = models.CharField(
        _("From email"),
        max_length=255,
        help_text=(
            _(
                "Example: MailBot &lt;mailbot@yourdomain.com&gt;<br />"
                "'From' header to set for outgoing email.<br />"
                "<br />"
                "If you do not use this e-mail inbox for outgoing mail, this "
                "setting is unnecessary.<br />"
                "If you send e-mail without setting this, your 'From' header will'"
                "be set to match the setting `DEFAULT_FROM_EMAIL`."
                "<br />"
                "Required for Office 365 mailbox"
            )
        ),
        blank=True,
        null=True,
        default=None,
    )

    active = models.BooleanField(
        _("Active"),
        help_text=(
            _(
                "Check this e-mail inbox for new e-mail messages during polling "
                "cycles.  This checkbox does not have an effect upon whether "
                "mail is collected here when this mailbox receives mail from a "
                "pipe, and does not affect whether e-mail messages can be "
                "dispatched from this mailbox. "
            )
        ),
        blank=True,
        default=True,
    )

    last_polling = models.DateTimeField(
        _("Last polling"),
        help_text=(
            _(
                "The time of last successful polling for messages."
                "It is blank for new mailboxes and is not set for "
                "mailboxes that only receive messages via a pipe."
            )
        ),
        blank=True,
        null=True,
    )

    objects = models.Manager()
    active_mailboxes = ActiveMailboxManager()

    @property
    def _protocol_info(self):
        return urlparse(self.uri)

    @property
    def _query_string(self):
        return parse_qs(self._protocol_info.query)

    @property
    def _domain(self):
        return self._protocol_info.hostname

    @property
    def port(self):
        """Returns the port to use for fetching messages."""
        return self._protocol_info.port

    @property
    def username(self):
        """Returns the username to use for fetching messages."""
        return unquote(self._protocol_info.username)

    @property
    def password(self):
        """Returns the password to use for fetching messages."""
        return unquote(self._protocol_info.password)

    @property
    def location(self):
        """Returns the location (domain and path) of messages."""
        return self._domain if self._domain else "" + self._protocol_info.path

    @property
    def type(self):
        """Returns the 'transport' name for this mailbox."""
        scheme = self._protocol_info.scheme.lower()
        if "+" in scheme:
            return scheme.split("+")[0]
        return scheme

    @property
    def use_ssl(self):
        """Returns whether or not this mailbox's connection uses SSL."""
        return "+ssl" in self._protocol_info.scheme.lower()

    @property
    def use_tls(self):
        """Returns whether or not this mailbox's connection uses STARTTLS."""
        return "+tls" in self._protocol_info.scheme.lower()

    @property
    def archive(self):
        """Returns (if specified) the folder to archive messages to."""
        archive_folder = self._query_string.get("archive", None)
        if not archive_folder:
            return None
        return archive_folder[0]

    @property
    def folder(self):
        """Returns (if specified) the folder to fetch mail from."""
        folder = self._query_string.get("folder", None)
        if not folder:
            return None
        return folder[0]

    def get_connection(self):
        """Returns the transport instance for this mailbox.

        These will always be instances of
        `django_mail_admin.transports.base.EmailTransport`.

        """
        if not self.uri:
            return None
        elif self.type == "imap":
            conn = ImapTransport(
                self.location,
                port=self.port if self.port else None,
                ssl=self.use_ssl,
                tls=self.use_tls,
                archive=self.archive,
                folder=self.folder,
            )
            conn.connect(self.username, self.password)
        elif self.type == "gmail":
            conn = GmailImapTransport(
                self.location,
                port=self.port if self.port else None,
                ssl=True,
                archive=self.archive,
            )
            conn.connect(self.username, self.password)
        elif self.type == O365Transport.SCHEME:  #'office365'
            conn = O365Transport(
                owner_email=self.from_email, last_polled=self.last_polling
            )
            conn.connect(
                client_app_id=self._query_string.get("client_app_id", [""])[0],
                client_id_key=self._query_string.get("client_id_key", [""])[0],
                client_secret_key=self._query_string.get("client_secret_key", [""])[0],
            )
        elif self.type == "pop3":
            conn = Pop3Transport(
                self.location, port=self.port if self.port else None, ssl=self.use_ssl
            )
            conn.connect(self.username, self.password)
        elif self.type == "maildir":
            conn = MaildirTransport(self.location)
        elif self.type == "mbox":
            conn = MboxTransport(self.location)
        elif self.type == "babyl":
            conn = BabylTransport(self.location)
        elif self.type == "mh":
            conn = MHTransport(self.location)
        elif self.type == "mmdf":
            conn = MMDFTransport(self.location)
        return conn

    def process_incoming_message(self, message):
        """Process a message incoming to this mailbox."""
        msg = self._process_message(message)
        if msg is None:
            return None
        msg.save()

        message_received.send(sender=self, message=msg)

        return msg

    def _get_dehydrated_message(self, msg, record):
        from django_mail_admin.models import IncomingAttachment

        new = EmailMessage()
        if msg.is_multipart():
            for header, value in msg.items():
                new[header] = value
            for part in msg.get_payload():
                new.attach(self._get_dehydrated_message(part, record))
        elif (
            strip_unallowed_mimetypes()
            and not msg.get_content_type() in get_allowed_mimetypes()
        ):
            for header, value in msg.items():
                new[header] = value
            # Delete header, otherwise when attempting to  deserialize the
            # payload, it will be expecting a body for this.
            del new["Content-Transfer-Encoding"]
            new[
                get_altered_message_header()
            ] = "Stripped; Content type %s not allowed" % (msg.get_content_type())
            new.set_payload("")
        elif (msg.get_content_type() not in get_text_stored_mimetypes()) or (
            "attachment" in msg.get("Content-Disposition", "")
        ):
            filename = None
            raw_filename = msg.get_filename()
            logger.info(
                f"Processing attachment for incoming email id {record.pk} with raw_filename {raw_filename}"
            )
            if raw_filename:
                filename = utils.convert_header_to_unicode(raw_filename)
            if not filename:
                extension = mimetypes.guess_extension(msg.get_content_type())
            else:
                _, extension = os.path.splitext(filename)
            if not extension:
                extension = ".bin"

            attachment = IncomingAttachment()
            logger.info(
                f"Saving attachment document for incoming email id {record.pk} with filename {filename}"
            )
            attachment.document.save(
                uuid.uuid4().hex + extension,
                ContentFile(BytesIO(msg.get_payload(decode=True)).getvalue()),
            )
            logger.info(
                f"Attachment document saved for incoming email id {record.pk} with filename {filename}"
            )
            attachment.message = record
            for key, value in msg.items():
                attachment[key] = value
            attachment.save()
            logger.info(
                f"Attachment created and saved for incoming email id {record.pk} with filename {filename}"
            )

            placeholder = EmailMessage()
            placeholder[get_attachment_interpolation_header()] = str(attachment.pk)
            new = placeholder
        else:
            content_charset = msg.get_content_charset()
            if not content_charset:
                content_charset = "ascii"
            try:
                # Make sure that the payload can be properly decoded in the
                # defined charset, if it can't, let's mash some things
                # inside the payload :-\
                msg.get_payload(decode=True).decode(content_charset)
            except LookupError:
                logger.warning(
                    "Unknown encoding %s; interpreting as ASCII!", content_charset
                )
                msg.set_payload(msg.get_payload(decode=True).decode("ascii", "ignore"))
            except ValueError:
                logger.warning(
                    "Decoding error encountered; interpreting %s as ASCII!",
                    content_charset,
                )
                msg.set_payload(msg.get_payload(decode=True).decode("ascii", "ignore"))
            new = msg
        return new

    def _process_message(self, message):
        from django_mail_admin.models import IncomingEmail, OutgoingEmail

        message_id = None
        if "message-id" in message:
            try:
                message_id = message["message-id"][0:255].strip()
            except Exception as e:
                message_id = None
        if not message_id:
            message_id = uuid.uuid4().hex

        msg, created = IncomingEmail.objects.get_or_create(
            mailbox=self, message_id=message_id
        )

        if not created:
            return msg

        if get_store_original_message():
            self._process_save_original_message(message, msg)
        msg.mailbox = self
        msg.message_id = message_id
        if "subject" in message:
            msg.subject = (
                utils.convert_header_to_unicode(message["subject"])
                .replace("\n", "")
                .replace("\r", "")[0:255]
            )
        if "from" in message:
            msg.from_header = utils.convert_header_to_unicode(message["from"])
        if "to" in message:
            msg.to_header = utils.convert_header_to_unicode(message["to"])
        elif "Delivered-To" in message:
            msg.to_header = utils.convert_header_to_unicode(message["Delivered-To"])

        if "Date" in message:
            try:
                date_str = message["Date"]
                sent_datetime = parser.parse(date_str)
                if sent_datetime is None:
                    sent_datetime = dateparser.parse(date_str)
                if sent_datetime:
                    msg.sent_datetime = sent_datetime
            except Exception as e:
                logger.warning(f"Failed to parse date for incoming email {msg.pk}: {e}")
        if msg.sent_datetime is None:
            msg.sent_datetime = msg.processed

        msg.save()
        message = self._get_dehydrated_message(message, msg)
        try:
            body = message.as_string()
        except KeyError as exc:
            # email.message.replace_header may raise 'KeyError' if the header
            # 'content-transfer-encoding' is missing
            logger.warning(
                "Failed to parse message: %s",
                exc,
            )
            return None
        msg.set_body(body)
        if message["in-reply-to"]:
            try:
                in_reply_to = message["in-reply-to"].strip()
                # Hack to work with db-independent JSONField (which is interpreted as string in db)
                msg.in_reply_to = OutgoingEmail.objects.filter(
                    headers__contains='"Message-ID": "'
                    + message["in-reply-to"].strip()
                    + '"'
                )[0]
            except IndexError:
                pass
        msg.save()
        return msg

    def _process_save_original_message(self, message, msg):
        if get_compress_original_message():
            with NamedTemporaryFile(suffix=".eml.gz") as fp_tmp:
                with gzip.GzipFile(fileobj=fp_tmp, mode="w") as fp:
                    fp.write(message.as_string().encode("utf-8"))
                msg.eml.save("%s.eml.gz" % (uuid.uuid4(),), File(fp_tmp), save=False)

        else:
            msg.eml.save(
                "%s.eml" % uuid.uuid4(),
                ContentFile(BytesIO(message.as_bytes()).getvalue()),
                save=False,
            )

    def get_new_mail(self, condition=None):
        """Connect to this transport and fetch new messages."""
        new_mail = []
        connection = self.get_connection()
        if not connection:
            return new_mail
        for message in connection.get_message(condition):
            msg = self.process_incoming_message(message)
            if msg is not None:
                new_mail.append(msg)
        self.last_polling = now()
        self.save(update_fields=["last_polling"])
        return new_mail

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Mailbox")
        verbose_name_plural = _("Mailboxes")
