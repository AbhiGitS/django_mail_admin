"""
EmailTransport implementation for Nylas
"""
import logging

from datetime import datetime

from django_mail_admin.nylas_utils import NylasConnection, get_nylas_grant_backend

from .base import EmailTransport

logger = logging.getLogger(__name__)


class NylasTransport(EmailTransport):
    SCHEME = NylasConnection.SCHEME

    def __init__(self, owner_email: str, last_polled: datetime = None) -> None:
        super().__init__()
        self.conn: NylasConnection = None
        self.owner_email: str = owner_email
        self.last_polled: datetime | None = last_polled

    def connect(self, uri_grant_id: str = None) -> None:
        """
        Establish connection to Nylas.

        Automatically tries to use grant backend for security if configured.
        Falls back to uri_grant_id if backend not available.

        Args:
            uri_grant_id: Grant ID from URI (fallback for backward compatibility)
        """
        try:
            # Try to use grant backend (preferred, secure)
            grant_backend = (
                get_nylas_grant_backend(self.owner_email) if self.owner_email else None
            )

            if grant_backend:
                # Use secure blob storage
                logger.debug(f"Using grant backend for {self.owner_email}")
                self.conn = NylasConnection(
                    from_email=self.owner_email, grant_backend=grant_backend
                )
            elif uri_grant_id:
                # Fallback to URI-based (deprecated)
                logger.debug(f"Using URI-based grant_id for {self.owner_email}")
                self.conn = NylasConnection(
                    from_email=self.owner_email, grant_id=uri_grant_id
                )
            else:
                raise ValueError(
                    f"No grant available for {self.owner_email}. "
                    "Configure NYLAS_GRANT_BACKEND or provide uri_grant_id."
                )
        except (TypeError, ValueError) as e:
            logger.warning("Couldn't authenticate with Nylas: %s" % e)

    def get_message(self, condition):
        """Yield messages from Nylas API with grant validation"""
        if not self.conn:
            logger.error("get_message unavailable; account not connected yet")
            return

        # Note: Grant validation happens automatically in NylasConnection.get_messages()
        # Exceptions (NylasGrantExpired, NylasGrantInvalid) will bubble up to caller
        for mail in self.conn.get_messages(
            last_polled=self.last_polled, condition=condition
        ):
            yield (self.get_email_from_bytes(mail))
