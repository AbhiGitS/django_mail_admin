"""
Nylas Grant model for tracking OAuth grants
"""
from django.db import models
from django.utils.translation import gettext_lazy as _
from jsonfield import JSONField


class NylasGrant(models.Model):
    """
    Store Nylas grant information for OAuth-connected mailboxes.

    This model tracks grant metadata, status, and provider information
    to enable grant validation and refresh workflows.
    """

    GRANT_STATUS_CHOICES = [
        ("valid", _("Valid")),
        ("invalid", _("Invalid")),
        ("needs_reauth", _("Needs Re-authentication")),
        ("expired", _("Expired")),
    ]

    mailbox = models.OneToOneField(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="nylas_grant",
        verbose_name=_("Mailbox"),
        help_text=_("The mailbox associated with this Nylas grant"),
    )

    grant_id = models.CharField(
        _("Grant ID"),
        max_length=255,
        unique=True,
        help_text=_("Nylas grant identifier"),
    )

    email = models.EmailField(
        _("Email Address"), help_text=_("The email address associated with this grant")
    )

    provider = models.CharField(
        _("Email Provider"),
        max_length=50,
        help_text=_("Email provider (e.g., google, microsoft, imap)"),
    )

    grant_status = models.CharField(
        _("Grant Status"),
        max_length=50,
        choices=GRANT_STATUS_CHOICES,
        default="valid",
        help_text=_("Current status of the grant"),
    )

    metadata = JSONField(
        _("Grant Metadata"),
        default=dict,
        blank=True,
        help_text=_("Additional grant information from Nylas"),
    )

    created_at = models.DateTimeField(
        _("Created At"), auto_now_add=True, help_text=_("When the grant was created")
    )

    updated_at = models.DateTimeField(
        _("Updated At"), auto_now=True, help_text=_("When the grant was last updated")
    )

    class Meta:
        verbose_name = _("Nylas Grant")
        verbose_name_plural = _("Nylas Grants")
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.email} ({self.provider}) - {self.grant_status}"

    def is_valid(self) -> bool:
        """Check if grant is currently valid"""
        return self.grant_status == "valid"

    def mark_invalid(self, reason: str = "invalid"):
        """Mark grant as invalid"""
        self.grant_status = reason
        self.save(update_fields=["grant_status", "updated_at"])

    def mark_valid(self):
        """Mark grant as valid"""
        self.grant_status = "valid"
        self.save(update_fields=["grant_status", "updated_at"])
