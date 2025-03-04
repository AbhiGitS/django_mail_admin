import logging

from django.core.management.base import BaseCommand

from django_mail_admin.models import Mailbox

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class Command(BaseCommand):
    def handle(self, *args, **options):
        #mailboxes = Mailbox.active_mailboxes.all()
        mailboxes = Mailbox.objects.all()
        if args:
            mailboxes = mailboxes.filter(
                name=' '.join(args)
            )
        for mailbox in mailboxes:
            logger.info(
                'Gathering messages for %s',
                mailbox.name
            )
            messages = mailbox.get_new_mail()
            for message in messages:
                logger.info(
                    'Received %s (from %s)',
                    message.subject,
                    message.from_address
                )
            if len(messages) == 0:
                logger.info('No new mail for %s', mailbox.name)
