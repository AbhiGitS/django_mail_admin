"""
Helper utility classes/ functions for O365 support
"""
import logging

from base64 import b64encode
from datetime import datetime
from django.conf import settings
from django.core.mail.message import EmailMessage

from O365 import MSGraphProtocol
from O365 import Account, Message, FileSystemTokenBackend
from O365.mailbox import MailBox

logger = logging.getLogger(__name__)

class O365Connection:
    scheme = "office365"
    o365_protocol = "MSGraphProtocol"

    def __init__(self,
                 client_id_key:str, 
                 client_secret_key:str, 
                 protocol=o365_protocol) -> None:
        self.account = None
        try:
            client_id, client_secret = self._get_auth_info(client_id_key, client_secret_key)
            self._connect(client_id, client_secret, protocol)
        except (TypeError, ValueError) as e:
            logger.warning("O365Connection: Couldn't authenticate %s" % e)

    def _get_auth_info(self, client_id_key:str, client_secret_key:str):
        if not settings.O365_ADMIN_SETTINGS:
            raise Exception("O365_ADMIN_SETTINGS configuration missing.")
        
        client_id = settings.O365_ADMIN_SETTINGS.get(client_id_key,"")
        if not client_id:
            raise Exception(f"O365_ADMIN_SETTINGS.{client_id_key} not set! ")
        
        client_secret = settings.O365_ADMIN_SETTINGS.get(client_secret_key,"")
        if not client_secret:
            raise Exception(f"O365_ADMIN_SETTINGS.{client_secret_key} not set!")
    
        return client_id, client_secret

    def _connect(self, client_id, client_secret, protocol) -> None:
        # connect_id/ and secret should have been already setup 
        # for offline & message_all scopes, for on behalf of user access.
        protocol_selected= MSGraphProtocol(api_version='beta') if protocol == self.o365_protocol else None
        if not protocol_selected:
            raise Exception(f"Unsupported protocol {protocol}")
        
        token_path=settings.O365_ADMIN_SETTINGS.get("O365_AUTH_BACKEND_TOKEN_DIR")
        if not token_path:
            token_path = "."
            logger.warning(f"Using default path '{token_path}'; highly recommended to set explicit path in O365_ADMIN_SETTINGS.O365_AUTH_BACKEND_TOKEN_DIR")
        
        token_filename=settings.O365_ADMIN_SETTINGS.get("O365_AUTH_BACKEND_TOKEN_FILE")
        if not token_filename:
            token_filename = "o365_token.txt"
            logger.warning(f"Using default token filename {token_filename}; highly recommended to set explicit path in O365_ADMIN_SETTINGS.O365_AUTH_BACKEND_TOKEN_DIR")
        
        token_backend = FileSystemTokenBackend(
            token_path=token_path,
            token_filename=token_filename
        )
        
        self.account = Account(credentials=(client_id, client_secret), protocol=protocol_selected, scopes=['offline_access', 'message_all'], token_backend=token_backend)
        
        if not self.account.is_authenticated:
            logger.info("Hold on .... going for authentication!")
            self.account.authenticate()

    def _get_message_by_id(self, mailbox:MailBox, message_id:str, folder_name="Inbox") -> Message|None:
        """retrieve message by id from given mailbox"""
        if not message_id:
            return None
        mail_folder = mailbox.get_folder(folder_name=folder_name) if mailbox else None
        qstr = f"internetMessageId eq '{message_id}'"
        emails = mail_folder.get_messages(query=qstr) if mail_folder else []
        for email in emails:
            return email
        return None

    def _get_reply_to_message(self, mailbox:MailBox, msg: EmailMessage) -> Message|None:
        """retrieve message representing in-reply-to id in msg headers"""
        if not msg or not msg.extra_headers or not mailbox:
            return None
        msg_id = msg.extra_headers.get('In-Reply-To', None)
        reply_to_msg = self._get_message_by_id( mailbox, msg_id ) if msg_id else None
        return reply_to_msg
    
    def _prepare_new_message(self, mailbox, msg:EmailMessage):
        """create new or reply-to draft based on msg in-reply-to header"""
        reply_to_message = self._get_reply_to_message(mailbox, msg)
        new_draft_message = reply_to_message.reply() if reply_to_message else mailbox.new_message()
        return new_draft_message
 
    def _prepare_attachment_for_dispatch(self, attachment) -> dict:
        """bridge from Django EmailMessage Attachment to O365 Attachment"""
        content=attachment[1]
        b64content=b64encode( content if isinstance(content, bytes) else bytes(content, 'utf-8') ).decode('utf-8')
        return { "name": f"{attachment[0]}"
                , "content": b64content
                , "on_disk": False
        }
    
    def send_messages(self, 
                      from_email:str, 
                      email_messages, 
                      fail_silently:bool = False) -> int:
        if not from_email:
            raise ValueError("from_email can not be empty!")

        sent_messages = 0
        mailbox = self.account.mailbox(from_email)
        for msg in email_messages:
            try:
                m = self._prepare_new_message(mailbox, msg)
                m.to.add(msg.to)
                m.cc.add(msg.cc)
                m.bcc.add(msg.bcc)
                m.reply_to.add(msg.reply_to)
                m.body = msg.body
                if msg.subject:
                    m.subject = msg.subject
                m.save_message()
                m.attachments.add( [self._prepare_attachment_for_dispatch(attachment) for attachment in msg.attachments] )
                for attachment in m.attachments:
                    #workaround: avoid NoneType compare w/ int, O365 exception
                    attachment.size = len(attachment.content)
                m.save_draft()
                m.send()
                sent_messages += 1
            except Exception as e:
                logger.error(f"Exception in sending message: error info: {e}")
                if not fail_silently:
                    raise e
        return sent_messages
    
    def get_messages(self, owner_email:str, last_polled: datetime, condition):
        if not self.account.is_authenticated:
            logger.error(f"get_messages unavailable; account not authenticated!")
            return
        mailbox = self.account.mailbox(owner_email)
        mail_folder = mailbox.get_folder(folder_name="Inbox")
        if last_polled:
            #ISO 8601 format AND in UTC time. 
            #For example, midnight UTC on Jan 1, 2022 is 2022-01-01T00:00:00Z.
            qstr=f"receivedDateTime gt {last_polled.replace(microsecond=0).isoformat()[:-6]}Z"
        for mail in mail_folder.get_messages(query=qstr):
            yield(mail.get_mime_content())
