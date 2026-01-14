"""
EmailTransport implementation for Nylas
"""
import logging

from datetime import datetime

from django_mail_admin.nylas_utils import NylasConnection

from .base import EmailTransport

logger = logging.getLogger(__name__)


class NylasTransport(EmailTransport):
    SCHEME = NylasConnection.SCHEME

    def __init__(self, owner_email: str, last_polled: datetime = None) -> None:
        super().__init__()
        self.conn: NylasConnection = None
        self.owner_email: str = owner_email
        self.last_polled: datetime | None = last_polled

    def connect(self, grant_id: str) -> None:
        """
        Establish connection to Nylas using grant_id from URI
        The NYLAS_API_KEY is retrieved from Django settings
        """
        try:
            self.conn = NylasConnection(from_email=self.owner_email, grant_id=grant_id)
        except (TypeError, ValueError) as e:
            logger.warning("Couldn't authenticate with Nylas: %s" % e)

    def get_message(self, condition):
        """Yield messages from Nylas API"""
        if not self.conn:
            logger.error("get_message unavailable; account not connected yet")
            return

        for mail in self.conn.get_messages(
            last_polled=self.last_polled, condition=condition
        ):
            yield (self.get_email_from_bytes(mail))
