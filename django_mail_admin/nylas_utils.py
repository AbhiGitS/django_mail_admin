"""
Helper utility classes/functions for Nylas support
"""
import logging
from typing import Optional
from datetime import datetime
from base64 import b64encode, urlsafe_b64decode

from django.conf import settings
from django.core.mail.message import EmailMessage, EmailMultiAlternatives
from django_mail_admin.exceptions import (
    NylasNotAuthenticated,
    NylasGrantExpired,
    NylasGrantInvalid,
    NylasException,
)

logger = logging.getLogger(__name__)


class NylasConnection:
    SCHEME = "nylas"
    API_VERSION = "v3"
    MESSAGE_ID_KEY = "Message-ID"

    def __init__(self, from_email: str, grant_id: str) -> None:
        self.from_email = from_email
        self.grant_id = grant_id
        if not self.from_email:
            raise ValueError("from_email required.")
        if not self.grant_id:
            raise ValueError("grant_id required.")

        self.api_key = self._get_api_key_from_settings()
        self.api_uri = self._get_api_uri_from_settings()
        self.client = None
        try:
            self._connect()
        except (TypeError, ValueError) as e:
            logger.warning("NylasConnection: Couldn't authenticate %s" % e)

    def _get_api_key_from_settings(self) -> str:
        """Retrieve NYLAS_API_KEY from Django settings"""
        api_key = getattr(settings, "NYLAS_API_KEY", None)
        if not api_key:
            raise ValueError("NYLAS_API_KEY not found in Django settings")
        return api_key

    def _get_api_uri_from_settings(self) -> str:
        """Get API URI (US or EU) from settings"""
        return getattr(settings, "NYLAS_API_URI", "https://api.us.nylas.com")

    def _connect(self) -> None:
        """Initialize Nylas SDK client"""
        try:
            from nylas import Client
        except ImportError:
            raise ValueError(
                "Install nylas package to use Nylas integration " "(pip install nylas)"
            )

        self.client = Client(api_key=self.api_key, api_uri=self.api_uri)

    @property
    def is_authenticated(self) -> bool:
        """Check if connection is authenticated"""
        return self.client is not None and self.grant_id is not None

    def validate_grant(self, mailbox=None):
        """
        Validate the grant status and raise appropriate exceptions if invalid.

        This method checks the grant status with Nylas API and updates the
        NylasGrant model if a mailbox reference is provided.

        Args:
            mailbox: Optional Mailbox model instance for status updates

        Raises:
            NylasGrantExpired: If grant has expired and needs re-authentication
            NylasGrantInvalid: If grant is invalid for other reasons
            NylasException: If there's an error checking grant status
        """
        try:
            grant_info = self.client.grants.find(self.grant_id)
            grant_data = grant_info.data if hasattr(grant_info, "data") else grant_info

            # Check grant status
            grant_status = getattr(grant_data, "grant_status", "unknown")

            if grant_status != "valid":
                # Update model status if we have mailbox reference
                if mailbox:
                    try:
                        from django_mail_admin.models.nylas_grant import NylasGrant

                        ng = NylasGrant.objects.get(mailbox=mailbox)
                        ng.mark_invalid(grant_status)
                    except:
                        pass

                # Raise appropriate exception
                if grant_status == "expired":
                    raise NylasGrantExpired(
                        grant_id=self.grant_id, email=self.from_email
                    )
                else:
                    raise NylasGrantInvalid(grant_id=self.grant_id, reason=grant_status)

        except (NylasGrantExpired, NylasGrantInvalid):
            # Re-raise these exceptions as-is
            raise
        except Exception as e:
            # Network or API error
            logger.error(f"Error validating grant: {e}")
            raise NylasException(f"Error validating grant {self.grant_id}: {e}")

    def get_messages(self, last_polled: datetime = None, condition=None):
        """Fetch messages from Nylas API with pagination and yield as MIME bytes"""
        if not (self.client and self.is_authenticated):
            logger.error("get_messages unavailable; account not authenticated!")
            raise NylasNotAuthenticated(
                "get_messages unavailable; account not yet authenticated!"
            )

        # Build query parameters
        query_params = {
            "limit": 50,  # Pagination limit per request (increased for efficiency)
        }

        if last_polled:
            # Nylas expects Unix timestamp
            query_params["received_after"] = int(last_polled.timestamp())

        try:
            # Initialize pagination
            page_token = None
            total_fetched = 0

            # Loop through all pages
            while True:
                # Add page token if we're fetching a subsequent page
                if page_token:
                    query_params["page_token"] = page_token

                # Fetch messages using Nylas SDK
                messages_response = self.client.messages.list(
                    identifier=self.grant_id, query_params=query_params
                )

                # Process messages in current page
                page_count = 0
                for message in messages_response.data:
                    # Get raw MIME content for each message
                    mime_content = self._get_raw_mime(message.id)
                    if mime_content:
                        yield urlsafe_b64decode(mime_content)
                        page_count += 1
                        total_fetched += 1

                logger.debug(
                    f"Fetched {page_count} messages from current page (total: {total_fetched})"
                )

                # Check if there are more pages
                # Nylas API v3 uses next_cursor for pagination
                if (
                    hasattr(messages_response, "next_cursor")
                    and messages_response.next_cursor
                ):
                    page_token = messages_response.next_cursor
                    logger.debug(
                        f"More pages available, continuing pagination with token: {page_token[:20]}..."
                    )
                else:
                    # No more pages, exit loop
                    logger.info(
                        f"Pagination complete. Total messages fetched: {total_fetched}"
                    )
                    break

        except Exception as e:
            logger.error(f"Error fetching messages from Nylas: {e}")
            raise

    def _get_raw_mime(self, message_id: str) -> Optional[bytes]:
        """Get raw MIME content for a specific message"""
        try:
            # Request raw MIME format from Nylas
            raw_message_response = self.client.messages.find(
                identifier=self.grant_id,
                message_id=message_id,
                query_params={"fields": "raw_mime"},
            )
            raw_message = raw_message_response.data
            # raw attribute contains the MIME message as bytes
            if hasattr(raw_message, "raw_mime") and raw_message.raw_mime:
                return (
                    raw_message.raw_mime.encode("utf-8")
                    if isinstance(raw_message.raw_mime, str)
                    else raw_message.raw_mime
                )
            return None
        except Exception as e:
            logger.error(f"Error fetching raw MIME for message {message_id}: {e}")
            return None

    def _get_html_body(self, msg: EmailMessage) -> Optional[str]:
        """Extract HTML body from EmailMessage if it exists"""
        if isinstance(msg, EmailMultiAlternatives):
            for msg_alt in msg.alternatives:
                alt_content, alt_content_type = msg_alt
                if "text/html" == alt_content_type:
                    return alt_content
        return None

    def _prepare_attachment(self, attachment) -> dict:
        """Convert Django EmailMessage attachment to Nylas format"""
        # attachment is a tuple: (filename, content, mimetype)
        filename = attachment[0]
        content = attachment[1]
        mimetype = attachment[2] if len(attachment) > 2 else "application/octet-stream"

        # Ensure content is bytes
        if isinstance(content, str):
            content = content.encode("utf-8")

        # Base64 encode the content
        b64content = b64encode(content).decode("utf-8")

        return {
            "filename": filename,
            "content": b64content,
            "content_type": mimetype,
        }

    def _prepare_recipients(self, email_list) -> list:
        """Convert email list to Nylas recipient format"""
        if not email_list:
            return []

        recipients = []
        for email in email_list:
            if isinstance(email, str):
                recipients.append({"email": email})
            elif isinstance(email, dict):
                recipients.append(email)
        return recipients

    def send_message(self, email_message: EmailMessage) -> bool:
        """Send email via Nylas API"""
        if not (self.client and self.is_authenticated):
            logger.error("send_message unavailable; account not authenticated!")
            raise NylasNotAuthenticated(
                "send_message unavailable; account not yet authenticated!"
            )

        try:
            # Determine body content (prefer HTML if available)
            html_body = self._get_html_body(email_message)
            text_body = email_message.body if not html_body else None

            # Prepare message payload
            message_payload = {
                "to": self._prepare_recipients(email_message.to),
                "subject": email_message.subject or "",
            }

            # Add optional recipients
            if email_message.cc:
                message_payload["cc"] = self._prepare_recipients(email_message.cc)
            if email_message.bcc:
                message_payload["bcc"] = self._prepare_recipients(email_message.bcc)
            if hasattr(email_message, "reply_to") and email_message.reply_to:
                message_payload["reply_to"] = self._prepare_recipients(
                    email_message.reply_to
                )

            # Add body content
            if html_body:
                message_payload["body"] = html_body
            elif text_body:
                message_payload["body"] = text_body

            # Add attachments
            if email_message.attachments:
                message_payload["attachments"] = [
                    self._prepare_attachment(att) for att in email_message.attachments
                ]

            # Add custom headers if present
            if hasattr(email_message, "extra_headers") and email_message.extra_headers:
                # Nylas may support custom headers - check API documentation
                # For now, we'll log them
                logger.debug(f"Extra headers present: {email_message.extra_headers}")

            # Send via Nylas
            sent_message_response = self.client.messages.send(
                identifier=self.grant_id, request_body=message_payload
            )
            sent_message = sent_message_response.data if sent_message_response else None

            # Update message-id header if we get one back
            if sent_message and hasattr(sent_message, "id"):
                logger.info(f"Message sent successfully via Nylas: {sent_message.id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Exception in sending message via Nylas: {e}")
            raise e
