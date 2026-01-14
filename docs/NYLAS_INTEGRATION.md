# Nylas Integration Guide for Django Mail Admin

## Overview

Django Mail Admin now supports **Nylas** as an email service provider. Nylas is a SaaS platform that provides unified REST API access to popular email services including Gmail, Outlook, Office 365, IMAP, and more.

### Benefits of Using Nylas

- **Unified API**: One integration supports multiple email providers (Gmail, Outlook, IMAP, Exchange)
- **Better Rate Limiting**: Nylas handles provider-specific rate limits automatically
- **Simplified OAuth**: No need for separate OAuth flows per provider
- **Reliability**: Built-in retry logic and connection management
- **Enhanced Features**: Access to calendars, contacts, and other features (future enhancement)

---

## Prerequisites

1. **Nylas Account**: Sign up at [https://nylas.com](https://nylas.com)
2. **API Key**: Obtain your Nylas API key from the Nylas Dashboard
3. **Grant IDs**: Create grants for each email account you want to connect

---

## Installation

### 1. Install Nylas SDK

The Nylas Python SDK is included in the requirements:

```bash
pip install nylas>=6.0.0
```

Or install from requirements.txt:

```bash
pip install -r requirements.txt
```

### 2. Configure Django Settings

Add Nylas configuration to your Django `settings.py`:

```python
# Nylas Configuration
NYLAS_API_KEY = "nyk_v0_your_actual_api_key_here"
NYLAS_API_URI = "https://api.us.nylas.com"  # or https://api.eu.nylas.com for EU

# Add Nylas backend to DJANGO_MAIL_ADMIN
DJANGO_MAIL_ADMIN = {
    "BACKENDS": {
        "default": "django_mail_admin.backends.CustomEmailBackend",
        "smtp": "django_mail_admin.backends.SMTPOutboxBackend",
        "o365": "django_mail_admin.backends.O365Backend",
        "gmail": "django_mail_admin.backends.GmailOAuth2Backend",
        "nylas": "django_mail_admin.backends.NylasBackend",  # Add this
    }
}
```

### 3. Environment Variables (Recommended)

For better security, use environment variables:

**.env file:**
```bash
NYLAS_API_KEY=nyk_v0_your_actual_api_key_here
NYLAS_API_URI=https://api.us.nylas.com
# For EU region, use: https://api.eu.nylas.com
```

**settings.py:**
```python
from decouple import config

NYLAS_API_KEY = config("NYLAS_API_KEY")
NYLAS_API_URI = config("NYLAS_API_URI", default="https://api.us.nylas.com")
```

---

## Configuration

### Setting Up Grants in Nylas

1. **Log in to Nylas Dashboard**: [https://dashboard.nylas.com](https://dashboard.nylas.com)
2. **Create a New Application** (if you haven't already)
3. **Connect Email Accounts**:
   - For each email account, create a "Grant"
   - Complete the OAuth flow for Gmail, Outlook, etc.
   - Or configure IMAP/SMTP credentials for generic accounts
4. **Copy the Grant ID**: You'll need this for the Mailbox/Outbox configuration

### Mailbox Configuration (Receiving Emails)

Configure a Mailbox in Django Admin:

- **Name**: `Sales Team - Nylas`
- **URI**: `nylas:sales@company.com:/?grant_id=your-grant-id-here`
- **From Email**: `sales@company.com`
- **Active**: ✓ (checked)

**URI Format:**
```
nylas:<email_address>:/?grant_id=<your-grant-id>
```

**Example:**
```
nylas:john@example.com:/?grant_id=abc123xyz789
```

### Outbox Configuration (Sending Emails)

Configure an Outbox in Django Admin:

- **Name**: `Sales Team Outbox - Nylas`
- **EMAIL_HOST**: `nylas://api.nylas.com?grant_id=your-grant-id-here`
- **EMAIL_HOST_USER**: `sales@company.com`
- **EMAIL_HOST_PASSWORD**: `not_used` (any value, required by model)
- **EMAIL_PORT**: `443` (required field, not used for Nylas)
- **EMAIL_USE_TLS**: `False` (not applicable for API)
- **EMAIL_USE_SSL**: `False` (not applicable for API)

**EMAIL_HOST Format:**
```
nylas://api.nylas.com?grant_id=<your-grant-id>
```

**Example:**
```
nylas://api.nylas.com?grant_id=abc123xyz789
```

---

## Usage Examples

### Receiving Emails

Use the `get_new_mail` management command:

```bash
python manage.py get_new_mail
```

This will fetch new emails from all active Nylas mailboxes.

### Sending Emails

**Using Django Mail Admin API:**

```python
from django_mail_admin import mail
from django_mail_admin.models import PRIORITY

email = mail.send(
    'sender@company.com',
    'recipient@example.com',
    subject='Test Email via Nylas',
    message='This email was sent through Nylas API',
    priority=PRIORITY.now,
    html_message='<h1>Hello from Nylas!</h1>',
    backend='nylas',  # Specify Nylas backend
)
```

**Using Django's send_mail:**

```python
from django.core.mail import send_mail

send_mail(
    'Subject here',
    'Here is the message.',
    'from@company.com',
    ['to@example.com'],
    fail_silently=False,
)
```

**With Attachments:**

```python
from django.core.mail import EmailMessage

email = EmailMessage(
    'Subject',
    'Body goes here',
    'from@company.com',
    ['to1@example.com', 'to2@example.com'],
    cc=['cc@example.com'],
    bcc=['bcc@example.com'],
)
email.attach_file('/path/to/file.pdf')
email.send()
```

---

## Multi-Account Setup

For organizations with multiple email accounts:

### Single API Key, Multiple Grant IDs

**Settings (one time):**
```python
NYLAS_API_KEY = "nyk_v0_common_key_for_all_accounts"
NYLAS_API_URI = "https://api.us.nylas.com"
```

**Multiple Mailboxes:**
- **Mailbox 1**: `nylas:sales@company.com:/?grant_id=grant_sales_123`
- **Mailbox 2**: `nylas:support@company.com:/?grant_id=grant_support_456`
- **Mailbox 3**: `nylas:info@company.com:/?grant_id=grant_info_789`

Each mailbox shares the same `NYLAS_API_KEY` but has a unique `grant_id`.

---

## Testing Connections

### Test Mailbox Connection

From Django Admin:
1. Go to **Mailboxes**
2. Select your Nylas mailbox
3. Click **Test connection** action
4. Verify "Successfully connected" message

From command line:
```bash
python manage.py test_connections
```

### Test Outbox Connection

From Django Admin:
1. Go to **Outboxes**
2. Select your Nylas outbox
3. Click **Test connection** action
4. Verify "Successfully connected" message

---

## Troubleshooting

### Common Issues

**1. "NYLAS_API_KEY not found in Django settings"**

- Ensure `NYLAS_API_KEY` is set in `settings.py`
- Check environment variables are loaded correctly
- Verify `.env` file is in the correct location

**2. "grant_id required"**

- Ensure the URI contains `grant_id` parameter
- Format: `nylas:user@example.com:/?grant_id=<grant-id>`

**3. "Nylas connection not authenticated"**

- Verify the grant_id is valid in Nylas Dashboard
- Check if the grant hasn't been revoked
- Ensure the API key has access to this grant

**4. "Install nylas package to use Nylas integration"**

- Run: `pip install nylas>=6.0.0`
- Or: `pip install -r requirements.txt`

### Debug Logging

Enable debug logging to troubleshoot issues:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django_mail_admin': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}
```

---

## Migration from Direct Provider Integration

### From Gmail Direct to Gmail via Nylas

**Before (Direct Gmail):**
```
URI: gmail+ssl://user@gmail.com:password@imap.gmail.com
```

**After (Gmail via Nylas):**
```
Settings: NYLAS_API_KEY = "nyk_v0_..."
URI: nylas:user@gmail.com:/?grant_id=grant_gmail_xyz
```

**Benefits:**
- No need for app-specific passwords
- Better rate limiting
- Unified API across providers

### From Office 365 Direct to Office 365 via Nylas

**Before (Direct O365):**
```
URI: office365:user@company.com:/?client_id_key=...&client_secret_key=...
```

**After (Office 365 via Nylas):**
```
Settings: NYLAS_API_KEY = "nyk_v0_..."
URI: nylas:user@company.com:/?grant_id=grant_o365_abc
```

---

## Advanced Features

### OAuth Mapping

For send-as scenarios (sending from a different email address):

1. Create an `EmailAddressOAuthMapping`:
```python
from django_mail_admin.models import EmailAddressOAuthMapping

EmailAddressOAuthMapping.objects.create(
    oauth_username='primary@company.com',
    send_as_email='alias@company.com'
)
```

2. Configure Mailbox/Outbox for `primary@company.com`
3. Send emails from `alias@company.com` - they'll use the primary account's grant

### Filtering and Conditions

When fetching emails, you can use conditions:

```python
mailbox = Mailbox.objects.get(name='Sales Team - Nylas')
new_messages = mailbox.get_new_mail(condition=None)
```

The `last_polling` datetime is automatically tracked and used to fetch only new messages.

---

## API Reference

### NylasConnection

**Location:** `django_mail_admin.nylas_utils.NylasConnection`

**Initialization:**
```python
from django_mail_admin.nylas_utils import NylasConnection

conn = NylasConnection(
    from_email='user@example.com',
    grant_id='your-grant-id'
)
```

**Methods:**
- `get_messages(last_polled=None, condition=None)` - Fetch messages
- `send_message(email_message)` - Send an email
- `is_authenticated` - Check authentication status

### NylasTransport

**Location:** `django_mail_admin.transports.nylas.NylasTransport`

**Usage:** Automatically used by Mailbox when URI scheme is `nylas:`

### NylasBackend

**Location:** `django_mail_admin.backends.NylasBackend`

**Usage:** Automatically used by Outbox when EMAIL_HOST contains `nylas`

---

## Support and Resources

- **Nylas Documentation**: [https://developer.nylas.com](https://developer.nylas.com)
- **Nylas Python SDK**: [https://github.com/nylas/nylas-python](https://github.com/nylas/nylas-python)
- **Django Mail Admin**: [https://github.com/Bearle/django_mail_admin](https://github.com/Bearle/django_mail_admin)
- **Report Issues**: Use GitHub Issues for Django Mail Admin

---

## FAQs

**Q: Can I use Nylas for both sending and receiving?**

A: Yes! Configure both a Mailbox (for receiving) and an Outbox (for sending) with the same grant_id.

**Q: Does Nylas support attachments?**

A: Yes, both sending and receiving attachments are fully supported.

**Q: What email providers does Nylas support?**

A: Gmail, Google Workspace, Outlook, Office 365, Exchange, IMAP, and more.

**Q: Is there a free tier for Nylas?**

A: Yes, Nylas offers a free tier with limitations. Check their pricing page for details.

**Q: Can I use multiple Nylas API keys?**

A: Currently, the integration uses a single `NYLAS_API_KEY` from settings. For multiple API keys, you'd need to customize the implementation.

**Q: How do I get a grant_id?**

A: Log into the Nylas Dashboard, connect an email account through OAuth or IMAP configuration, and copy the resulting grant ID.

---

## License

This integration follows the same license as Django Mail Admin.
