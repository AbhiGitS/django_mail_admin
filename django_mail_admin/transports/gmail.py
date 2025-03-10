import logging

from django_mail_admin.transports.imap import ImapTransport

logger = logging.getLogger(__name__)


class GmailImapTransport(ImapTransport):
    def connect(self, username, password):
        # Try to use oauth2 first.  It's much safer
        try:
            self._connect_oauth(username)
        except (TypeError, ValueError) as e:
            logger.warning("Couldn't do oauth2 because %s" % e)
            self.server = self.transport(self.hostname, self.port)
            typ, msg = self.server.login(username, password)
            self.server.select()

    def _connect_oauth(self, username):
        # username should be an email address that has already been authorized
        # for gmail access
        try:
            from django_mail_admin.google_utils import (
                get_google_access_token,
                fetch_user_info,
                AccessTokenNotFound,
            )
        except ImportError:
            raise ValueError(
                """Install python-social-auth/social-app-django to use oauth2 auth for gmail
                (pip install social-auth-app-django)"""
            )

        access_token = None
        google_email_address = None
        while access_token is None:
            try:
                google_email_address = fetch_user_info(username)["email"]
                access_token = get_google_access_token(username)
            except TypeError:
                # This means that the google process took too long
                # Trying again is the right thing to do
                pass
            except AccessTokenNotFound:
                raise ValueError(
                    "No Token available in python-social-auth for %s" % (username)
                )

        auth_string = "user=%s\1auth=Bearer %s\1\1" % (
            google_email_address,
            access_token,
        )
        self.server = self.transport("imap.gmail.com", self.port)
        self.server.authenticate("XOAUTH2", lambda x: auth_string)
        self.server.select()
