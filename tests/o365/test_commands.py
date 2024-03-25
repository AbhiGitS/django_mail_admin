import time
import random

from urllib import parse
from datetime import datetime

from django.test import TestCase
from django.conf import settings
from django.core.management import call_command

from django_mail_admin.models import Outbox, IncomingEmail, OutgoingEmail, STATUS
from django_mail_admin.models import Mailbox
from django_mail_admin.mail import send_queued


class O365CommandTest(TestCase):
    def print_incoming_emails(self):
        for email in IncomingEmail.objects.all().order_by("processed"):
            print(f"{email.message_id} | {email.subject} | {email.processed}")

    def test_o365(self):
        user1_email = settings.O365_TEST_ACCOUNT
        user1_email_str = parse.quote(user1_email)
        o365_con = f"office365://{user1_email_str}@outlook.office365.com?client_id_key=O365_CLIENT_ID&client_secret_key=O365_CLIENT_SECRET&archive=Archived"

        inbox = Mailbox.objects.create(
            name="O365CommandTest_Inbox", uri=o365_con, from_email=user1_email
        )
        inbox.get_new_mail()
        print(f"received {IncomingEmail.objects.count()} new emails")
        # self.print_incoming_emails()

        outbox = Outbox.objects.create(
            name="O365CommandTest_Outbox",
            email_host=o365_con,
            email_host_user=user1_email,
            email_host_password="ase123hgfd",
            active=True,
        )

        test_subject = f"UnitTest Subject Dated {datetime.now()}"
        test_body = f"UnitTest Body Random {random.random()}"
        """
        print(f"\ntest_subject: {test_subject}")
        print(f"test_body: {test_body}\n")
        """
        OutgoingEmail.objects.create(
            from_email=user1_email,
            to=[user1_email],
            status=STATUS.queued,
            subject=test_subject,
            message=test_body,
            backend_alias="o365",
        )
        # call_command("send_queued_mail", log_level=0)
        total_sent, total_failed = send_queued()
        assert total_failed == 0
        assert total_sent == 1
        time.sleep(5)
        max_attempts = 3
        latest_email = None
        while max_attempts:
            max_attempts -= 1
            new_emails = inbox.get_new_mail()
            if not new_emails:
                print(
                    f"new mail not seen yet; checking again, attempts remaining {max_attempts}"
                )
                time.sleep(5)
            else:
                latest_email = new_emails[0]
                break
        """
        print(f"\nlatest_email.subject: {latest_email.subject}")
        print(f"latest_email.text: {latest_email.text}")
        """
        assert latest_email
        assert latest_email.subject == test_subject
        assert latest_email.text == test_body
