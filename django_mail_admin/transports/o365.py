"""
EmailTransport implementation for O365
"""
import logging

from datetime import datetime

from django_mail_admin.o365_util import O365Connection

from .base import EmailTransport

logger = logging.getLogger(__name__)

class O365Transport(EmailTransport):
    scheme = O365Connection.scheme

    def __init__(self, last_polled: datetime = None) -> None:
        super().__init__()
        self.conn: O365Connection = None
        self.owner_email: str = None
        self.last_polled: datetime | None = last_polled

    def connect(self,
                client_id_key: str,
                client_secret_key: str,
                owner_email: str,
                o365_protocol="MSGraphProtocol" ) -> None:
        try:
            self.conn = O365Connection( client_id_key = client_id_key, 
                                    client_secret_key = client_secret_key, 
                                    protocol = o365_protocol
                                )
            self.owner_email = owner_email
        except (TypeError, ValueError) as e:
            logger.warning("Couldn't authenticate %s" % e)

    def get_message(self, condition):
        if not self.conn:
            logger.error(f"get_message unavailable; account not connected yet")
            return
        for mail in self.conn.get_messages(self.owner_email, self.last_polled, condition):
            yield(self.get_email_from_bytes(mail))
