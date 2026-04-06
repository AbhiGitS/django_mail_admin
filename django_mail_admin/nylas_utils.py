"""
Helper utility classes/functions for Nylas support
"""
import logging
import hashlib
import json
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
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


# ==========================================
# Grant Backend Utility Functions
# ==========================================


def exchange_nylas_code_for_grant(code: str, redirect_uri: str, mailbox):
    """
    Exchange Nylas authorization code for grant and save to backend storage.

    This is a shared utility function used by both django_mail_admin and example OAuth callbacks.

    Args:
        code: Authorization code from Nylas OAuth callback
        redirect_uri: The redirect URI used in the OAuth flow
        mailbox: Mailbox instance to associate the grant with

    Returns:
        tuple: (success: bool, message: str, grant_data: dict or None)
    """
    from django.conf import settings

    try:
        from nylas import Client
    except ImportError:
        return False, "Nylas SDK not installed. Install with: pip install nylas", None

    # Get Nylas configuration from settings
    api_key = getattr(settings, "NYLAS_API_KEY", None)
    api_uri = getattr(settings, "NYLAS_API_URI", "https://api.us.nylas.com")
    client_id = getattr(settings, "NYLAS_CLIENT_ID", None)
    client_secret = getattr(settings, "NYLAS_CLIENT_SECRET", None)

    if not all([api_key, client_id, client_secret]):
        return (
            False,
            "Nylas configuration incomplete. Check NYLAS_API_KEY, NYLAS_CLIENT_ID, and NYLAS_CLIENT_SECRET in settings.",
            None,
        )

    try:
        client = Client(api_key=api_key, api_uri=api_uri)

        # Exchange authorization code for grant
        exchange_request = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
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
            return False, "Failed to obtain grant_id from Nylas response", None

        # Ensure from_email is set on mailbox
        if not mailbox.from_email:
            mailbox.from_email = email
            mailbox.save(update_fields=["from_email"])

        # Get grant backend
        grant_backend = get_nylas_grant_backend(mailbox.from_email)

        if not grant_backend:
            logger.warning(
                f"Grant backend not configured for {mailbox.from_email}, grant not saved to backend storage"
            )
            return (
                False,
                f"Grant backend not configured for {mailbox.from_email}. Check NYLAS_GRANT_BACKEND settings.",
                None,
            )

        # Extract metadata
        metadata = {}
        if hasattr(grant_data, "__dict__"):
            metadata = {
                k: v
                for k, v in grant_data.__dict__.items()
                if k not in ["grant_id", "email", "provider", "grant_status"]
            }

        # Save grant to backend storage
        success = grant_backend.save_grant(
            grant_id=grant_id,
            email=email or mailbox.from_email,
            provider=provider or "unknown",
            grant_status="valid",
            metadata=metadata,
        )

        if not success:
            logger.error(
                f"Failed to save grant to backend storage for {mailbox.from_email}"
            )
            return (
                False,
                f"Failed to save grant to backend storage for {mailbox.from_email}",
                None,
            )

        logger.info(
            f"Successfully authenticated mailbox {mailbox.id} with grant {grant_id}"
        )

        return (
            True,
            "Grant successfully created and saved",
            {
                "grant_id": grant_id,
                "email": email,
                "provider": provider,
                "mailbox": mailbox,
            },
        )

    except Exception as e:
        logger.error(
            f"Nylas grant exchange failed for mailbox {mailbox.id}: {e}", exc_info=True
        )
        return False, f"Grant exchange failed: {str(e)}", None


def get_nylas_grant_backend(from_email: str):
    """
    Get the Nylas grant backend configured in settings for a given email.

    This is a utility function that creates the appropriate grant backend
    based on Django settings configuration.

    Args:
        from_email: Email address to generate unique blob path

    Returns:
        BaseNylasGrantBackend or None: The configured backend instance
    """
    if not from_email:
        logger.warning(f"from_email is required for Nylas grant backend")
        return None

    backend_type = getattr(settings, "NYLAS_GRANT_BACKEND", None)
    if not backend_type:
        logger.debug("NYLAS_GRANT_BACKEND not configured in settings")
        return None

    backend_settings = getattr(settings, "NYLAS_GRANT_BACKENDS", {}).get(
        backend_type, {}
    )
    if not backend_settings:
        logger.warning(f"NYLAS_GRANT_BACKENDS['{backend_type}'] not configured")
        return None

    try:
        if backend_type == "AZBlobStorageNylasGrantBackend":
            return AZBlobStorageNylasGrantBackend(
                connection_str=backend_settings.get("NYLAS_GRANT_AZ_CONNECTION_STR"),
                container_name=backend_settings.get("NYLAS_GRANT_AZ_CONTAINER_PATH"),
                blob_name_pattern=backend_settings.get(
                    "NYLAS_GRANT_AZ_BLOB_NAME", "nylas_grant.json"
                ),
                from_email=from_email,
            )
    except Exception as e:
        logger.error(f"Failed to create Nylas grant backend for {from_email}: {e}")
        return None

    return None


# ==========================================
# Grant Backend Classes
# ==========================================


class BaseNylasGrantBackend(ABC):
    """Abstract base class for Nylas grant storage backends"""

    def __init__(self):
        self._grant_data: Optional[Dict[str, Any]] = None

    @abstractmethod
    def load_grant(self) -> Optional[Dict[str, Any]]:
        """
        Load grant data from storage.

        Returns:
            dict or None: Grant data if exists, None otherwise
                {
                    'grant_id': str,
                    'email': str,
                    'provider': str,
                    'grant_status': str,
                    'metadata': dict,
                    'created_at': str,
                    'updated_at': str
                }
        """
        pass

    @abstractmethod
    def save_grant(
        self,
        grant_id: str,
        email: str,
        provider: str,
        grant_status: str = "valid",
        metadata: dict = None,
    ) -> bool:
        """
        Save grant data to storage.

        Args:
            grant_id: The Nylas grant identifier
            email: Email address associated with grant
            provider: Email provider (google, microsoft, imap)
            grant_status: Status of grant (valid, invalid, expired, needs_reauth)
            metadata: Additional grant metadata

        Returns:
            bool: Success/Failure
        """
        pass

    @abstractmethod
    def delete_grant(self) -> bool:
        """
        Delete grant from storage.

        Returns:
            bool: Success/Failure
        """
        pass

    @abstractmethod
    def check_grant(self) -> bool:
        """
        Check if grant exists in storage.

        Returns:
            bool: True if exists, False otherwise
        """
        pass

    def is_valid(self) -> bool:
        """Check if grant is currently valid"""
        grant_data = self.load_grant()
        if grant_data:
            return grant_data.get("grant_status") == "valid"
        return False

    def mark_invalid(self, reason: str = "invalid") -> bool:
        """Mark grant as invalid"""
        grant_data = self.load_grant()
        if grant_data:
            grant_data["grant_status"] = reason
            grant_data["updated_at"] = datetime.utcnow().isoformat()
            return self._update_grant(grant_data)
        return False

    def mark_valid(self) -> bool:
        """Mark grant as valid"""
        grant_data = self.load_grant()
        if grant_data:
            grant_data["grant_status"] = "valid"
            grant_data["updated_at"] = datetime.utcnow().isoformat()
            return self._update_grant(grant_data)
        return False

    def _update_grant(self, grant_data: Dict[str, Any]) -> bool:
        """Helper to update existing grant data"""
        return self.save_grant(
            grant_id=grant_data.get("grant_id"),
            email=grant_data.get("email"),
            provider=grant_data.get("provider"),
            grant_status=grant_data.get("grant_status", "valid"),
            metadata=grant_data.get("metadata", {}),
        )


class AZBlobStorageNylasGrantBackend(BaseNylasGrantBackend):
    """Azure Blob Storage backend for Nylas grant management"""

    def __init__(
        self,
        connection_str: str,
        container_name: str,
        blob_name_pattern: str,
        from_email: str,
    ):
        """
        Initialize Azure Blob Storage backend for Nylas grants.

        Args:
            connection_str: Azure Storage connection string
            container_name: Azure blob container name
            blob_name_pattern: Pattern for blob filename (e.g., 'nylas_grant.json')
            from_email: Email address to generate unique blob path
        """
        if not (connection_str and container_name and blob_name_pattern and from_email):
            raise ValueError(
                "All parameters required for AZBlobStorageNylasGrantBackend: "
                f"connection_str={bool(connection_str)}, container_name={bool(container_name)}, "
                f"blob_name_pattern={bool(blob_name_pattern)}, from_email={bool(from_email)}"
            )

        self.from_email = from_email
        self.container_name = container_name

        # Get NYLAS_CLIENT_ID from settings to use as salt
        nylas_client_id = getattr(settings, "NYLAS_CLIENT_ID", None)
        if not nylas_client_id:
            raise ValueError("NYLAS_CLIENT_ID not found in Django settings")

        # Generate unique blob name using from_email + NYLAS_CLIENT_ID
        self.blob_name = self._generate_blob_name(
            from_email, nylas_client_id, blob_name_pattern
        )

        try:
            from azure.storage.blob import BlobClient
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "Please install azure-storage-blob package to use this backend."
            ) from e

        super().__init__()

        self._client = BlobClient.from_connection_string(
            connection_str,
            container_name=container_name,
            blob_name=self.blob_name,
        )

    def __repr__(self):
        return f"AZBlobStorageNylasGrantBackend('{self.container_name}', '{self.blob_name}')"

    def _generate_blob_name(
        self, from_email: str, nylas_client_id: str, blob_name_pattern: str
    ) -> str:
        """
        Generate unique blob name based on from_email + NYLAS_CLIENT_ID.

        Similar to O365's _decorate_token_name approach.
        Example: "abc123def456.../nylas_grant.json"

        Args:
            from_email: Email address
            nylas_client_id: Nylas Client ID (used as salt)
            blob_name_pattern: Filename pattern

        Returns:
            str: Unique blob path
        """
        # Create hash from from_email + NYLAS_CLIENT_ID
        hash_key = hashlib.sha256(
            (from_email + nylas_client_id).encode("utf-8")
        ).hexdigest()

        return f"{hash_key}/{blob_name_pattern}"

    def load_grant(self) -> Optional[Dict[str, Any]]:
        """
        Load grant data from Azure Blob Storage.

        Returns:
            dict or None: Grant data if exists, None otherwise
        """
        try:
            downloader = self._client.download_blob(max_concurrency=1, encoding="UTF-8")
            grant_str = downloader.readall()
            grant_data = json.loads(grant_str)
            logger.debug(f"Loaded grant for {self.from_email} from blob storage")
            return grant_data
        except Exception as e:
            logger.debug(
                f"Grant for {self.from_email} could not be retrieved from blob storage: {e}"
            )
            return None

    def save_grant(
        self,
        grant_id: str,
        email: str,
        provider: str,
        grant_status: str = "valid",
        metadata: dict = None,
    ) -> bool:
        """
        Save grant data to Azure Blob Storage.

        Args:
            grant_id: The Nylas grant identifier
            email: Email address associated with grant
            provider: Email provider (google, microsoft, imap)
            grant_status: Status of grant
            metadata: Additional grant metadata

        Returns:
            bool: Success/Failure
        """
        if not grant_id:
            raise ValueError("grant_id is required")

        now = datetime.utcnow().isoformat()

        # Check if grant exists to preserve created_at
        existing_grant = self.load_grant()
        created_at = existing_grant.get("created_at") if existing_grant else now

        grant_data = {
            "grant_id": grant_id,
            "email": email,
            "provider": provider,
            "grant_status": grant_status,
            "metadata": metadata or {},
            "created_at": created_at,
            "updated_at": now,
        }

        try:
            grant_str = json.dumps(grant_data, indent=2)
            self._client.upload_blob(grant_str, overwrite=True)
            logger.info(f"Saved grant for {self.from_email} to blob storage")
            return True
        except Exception as e:
            logger.error(f"Failed to save grant for {self.from_email}: {e}")
            return False

    def delete_grant(self) -> bool:
        """
        Delete grant from Azure Blob Storage.

        Returns:
            bool: Success/Failure
        """
        try:
            self._client.delete_blob()
            logger.warning(f"Deleted grant for {self.from_email} from blob storage")
            return True
        except Exception as e:
            logger.error(f"Failed to delete grant for {self.from_email}: {e}")
            return False

    def check_grant(self) -> bool:
        """
        Check if grant exists in Azure Blob Storage.

        Returns:
            bool: True if exists, False otherwise
        """
        try:
            return self._client.exists()
        except Exception:
            return False


class NylasConnection:
    SCHEME = "nylas"
    API_VERSION = "v3"
    MESSAGE_ID_KEY = "Message-ID"

    def __init__(
        self,
        from_email: str,
        grant_id: str = None,
        grant_backend: BaseNylasGrantBackend = None,
    ) -> None:
        self.from_email = from_email
        self.grant_backend = grant_backend

        if not self.from_email:
            raise ValueError("from_email required.")

        # If grant_backend provided, load grant from it
        if grant_backend:
            grant_data = grant_backend.load_grant()
            if grant_data:
                self.grant_id = grant_data.get("grant_id")
            else:
                self.grant_id = None
        else:
            self.grant_id = grant_id

        if not self.grant_id:
            raise ValueError("grant_id required but not provided/ or established")

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
        grant backend if a mailbox reference is provided.

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
                # Update backend status if available
                if self.grant_backend:
                    self.grant_backend.mark_invalid(grant_status)

                # Raise appropriate exception
                if grant_status == "expired":
                    raise NylasGrantExpired(
                        grant_id=self.grant_id, email=self.from_email
                    )
                else:
                    raise NylasGrantInvalid(grant_id=self.grant_id, reason=grant_status)
            else:
                # Grant is valid - update backend if available
                if self.grant_backend:
                    self.grant_backend.mark_valid()

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
