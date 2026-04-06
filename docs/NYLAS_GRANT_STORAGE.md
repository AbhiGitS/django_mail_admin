# Nylas Grant Secure Storage with Azure Blob Storage

## Overview

This document describes the secure storage solution for Nylas OAuth grants using Azure Blob Storage, replacing the insecure practice of storing sensitive grant data (including bearer tokens) directly in the database.

## Security Issue

Previously, the `NylasGrant` model stored sensitive data including:
- Grant IDs
- Bearer tokens
- OAuth credentials
- Provider metadata

Storing this data in the database poses security risks:
- ❌ Database dumps expose sensitive tokens
- ❌ Database backups contain credentials
- ❌ Direct database access exposes bearer tokens
- ❌ No encryption at rest by default
- ❌ Difficult to audit access

## Solution: AZBlobStorageNylasGrantBackend

The new `AZBlobStorageNylasGrantBackend` stores grant data securely in Azure Blob Storage:

✅ **Encryption**: Azure provides encryption at rest
✅ **Access Control**: Azure IAM controls who can access grant data
✅ **Audit Logging**: Azure logs all access for compliance
✅ **Secure Naming**: SHA256 hash prevents enumeration
✅ **No Database Exposure**: Tokens never stored in database

## Architecture

### Blob Naming Strategy

Grants are stored using a secure, deterministic naming scheme:

```
Blob Path: sha256(from_email + NYLAS_CLIENT_ID) / nylas_grant.json
```

**Example:**
- `from_email`: `user@example.com`
- `NYLAS_CLIENT_ID`: `nylas_client_abc123`
- Hash: `sha256("user@example.com:nylas_client_abc123")` = `d7f8e9a0b1c2...`
- Blob Path: `d7f8e9a0b1c2d3e4f5a6b7c8d9e0f1a2.../nylas_grant.json`

This approach:
- Uses `from_email` for portability across environments
- Uses `NYLAS_CLIENT_ID` as salt (already a required secret)
- Prevents enumeration of email addresses
- Creates unique storage per user

### Grant Data Structure

Each blob contains JSON with the following structure:

```json
{
  "grant_id": "nylas_grant_xyz789",
  "email": "user@example.com",
  "provider": "google",
  "grant_status": "valid",
  "metadata": {
    "scopes": ["mail.read", "mail.send"],
    "created_by": "oauth_flow"
  },
  "created_at": "2026-01-15T12:00:00",
  "updated_at": "2026-01-15T12:00:00"
}
```

## Configuration

### Django Settings

Add to your `settings.py`:

```python
# Nylas API Configuration
NYLAS_API_KEY = "your_nylas_api_key"
NYLAS_API_URI = "https://api.us.nylas.com"  # or https://api.eu.nylas.com
NYLAS_CLIENT_ID = "your_nylas_client_id"  # Also used as salt

# Grant Storage Backend
NYLAS_GRANT_BACKEND = 'AZBlobStorageNylasGrantBackend'

NYLAS_GRANT_BACKENDS = {
    'AZBlobStorageNylasGrantBackend': {
        'NYLAS_GRANT_AZ_CONNECTION_STR': 'DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net',
        'NYLAS_GRANT_AZ_CONTAINER_PATH': 'nylas-grants',
        'NYLAS_GRANT_AZ_BLOB_NAME': 'nylas_grant.json',
    },
}
```

### Environment Variables

Add to your `.env` file:

```bash
# Nylas Configuration
NYLAS_API_KEY=nyk_v0_...
NYLAS_API_URI=https://api.us.nylas.com
NYLAS_CLIENT_ID=your-nylas-client-id

# Grant Storage Backend
NYLAS_GRANT_BACKEND=AZBlobStorageNylasGrantBackend
NYLAS_GRANT_AZ_CONNECTION_STR=DefaultEndpointsProtocol=https;AccountName=...
NYLAS_GRANT_AZ_CONTAINER_PATH=nylas-grants
NYLAS_GRANT_AZ_BLOB_NAME=nylas_grant.json
```

## Migration from Database to Blob Storage

### Step 1: Configure Azure Blob Storage

Ensure your Azure Blob Storage container is created and accessible.

### Step 2: Update Settings

Add the `NYLAS_GRANT_BACKEND` configuration to your settings as shown above.

### Step 3: Run Migration Command (Dry Run)

First, test the migration without making changes:

```bash
python manage.py migrate_nylas_grants_to_blob --dry-run
```

This shows what will be migrated without actually migrating.

### Step 4: Run Actual Migration

```bash
python manage.py migrate_nylas_grants_to_blob
```

Output example:
```
Found 5 grant(s) to migrate...

✓ Migrated grant for 'user1@example.com' to blob storage
✓ Migrated grant for 'user2@example.com' to blob storage
✓ Migrated grant for 'user3@example.com' to blob storage
⚠ Skipping grant nylas_xyz: No from_email set on mailbox 'Test Mailbox'
ℹ Grant for 'user4@example.com' already exists in blob storage (skipping)

============================================================
Migration Summary:
  Total grants found: 5
  Migrated: 3
  Skipped: 2
  Errors: 0
============================================================

✓ Migration completed successfully!

NOTE: The NylasGrant database records have NOT been deleted.
They are kept for backward compatibility during the transition period.
You can safely delete them once you verify the blob storage is working correctly.
```

### Step 5: Verify

Verify that your Nylas integration still works:
- Send test emails via Nylas
- Receive emails via Nylas
- Check grant validation

### Step 6: Clean Up (Optional)

Once verified, you can optionally clean up the old database records:

```python
from django_mail_admin.models.nylas_grant import NylasGrant
# Review first
for grant in NylasGrant.objects.all():
    print(f"Grant: {grant.email} - {grant.grant_id}")

# Delete if confident
NylasGrant.objects.all().delete()
```

## Usage

### Programmatic Access

#### Get Grant Backend

```python
from django_mail_admin.models import Mailbox

mailbox = Mailbox.objects.get(from_email='user@example.com')
grant_backend = mailbox.get_nylas_grant_backend()
```

#### Load Grant Data

```python
grant_data = grant_backend.load_grant()
if grant_data:
    print(f"Grant ID: {grant_data['grant_id']}")
    print(f"Status: {grant_data['grant_status']}")
```

#### Save/Update Grant

```python
success = grant_backend.save_grant(
    grant_id='nylas_grant_xyz789',
    email='user@example.com',
    provider='google',
    grant_status='valid',
    metadata={'scopes': ['mail.read', 'mail.send']}
)
```

#### Check Grant Status

```python
if grant_backend.is_valid():
    print("Grant is valid")
else:
    print("Grant needs re-authentication")
```

#### Mark Grant Invalid

```python
grant_backend.mark_invalid(reason='expired')
```

### Mailbox Helper Methods

```python
# Get grant data
grant_data = mailbox.get_nylas_grant()

# Update grant
mailbox.update_nylas_grant(
    grant_id='nylas_grant_xyz789',
    email='user@example.com',
    provider='google',
    grant_status='valid'
)

# Get grant ID only
grant_id = mailbox.get_nylas_grant_id()
```

## Backward Compatibility

The implementation maintains backward compatibility:

1. **Priority Order**:
   - First: Check grant backend (preferred, secure)
   - Second: Check NylasGrant model (deprecated)
   - Third: Parse from Mailbox URI (deprecated)

2. **Graceful Fallback**: If grant backend isn't configured, falls back to database model

3. **Dual Storage**: During transition, both blob storage and database can coexist

4. **Deprecation Warnings**: `NylasGrant` model issues warnings when imported

## Best Practices

### Security

1. **Protect Connection Strings**: Never commit Azure connection strings to version control
2. **Use Environment Variables**: Store all sensitive config in `.env` files
3. **Restrict Access**: Use Azure IAM to limit who can access the blob container
4. **Enable Audit Logs**: Turn on Azure logging for compliance
5. **Rotate Keys**: Periodically rotate your Azure Storage account keys

### Operations

1. **Monitor Grant Status**: Regularly check grant validity
2. **Handle Expiration**: Implement re-authentication flows for expired grants
3. **Backup Strategy**: Azure provides redundancy, but consider backup policies
4. **Test Migrations**: Always use `--dry-run` first

### Development

1. **Local Development**: Can use Azure Storage Emulator or separate dev container
2. **Testing**: Mock the backend in unit tests
3. **Logging**: Enable debug logging to troubleshoot issues

## Troubleshooting

### Grant Not Found

**Issue**: `Grant for {email} could not be retrieved from blob storage`

**Solutions**:
- Verify Azure connection string is correct
- Check container name matches configuration
- Ensure blob exists for that email
- Verify `NYLAS_CLIENT_ID` hasn't changed

### Authentication Errors

**Issue**: Azure authentication fails

**Solutions**:
- Check connection string format
- Verify storage account key
- Ensure container exists
- Check network/firewall rules

### Migration Failures

**Issue**: Migration command fails

**Solutions**:
- Run with `--dry-run` to diagnose
- Check that mailboxes have `from_email` set
- Verify `NYLAS_GRANT_BACKEND` is configured
- Review error messages for specific issues

### Grant Status Issues

**Issue**: Grant shows as invalid when it should be valid

**Solutions**:
- Run grant validation: `conn.validate_grant()`
- Check Nylas API for actual grant status
- Review grant metadata in blob storage
- Re-authenticate if truly expired

## API Reference

### BaseNylasGrantBackend

Abstract base class for grant backends.

**Methods**:
- `load_grant()` → `dict | None`: Load grant data
- `save_grant(grant_id, email, provider, grant_status, metadata)` → `bool`: Save grant
- `delete_grant()` → `bool`: Delete grant
- `check_grant()` → `bool`: Check if grant exists
- `is_valid()` → `bool`: Check if grant is valid
- `mark_invalid(reason)` → `bool`: Mark grant as invalid
- `mark_valid()` → `bool`: Mark grant as valid

### AZBlobStorageNylasGrantBackend

Azure Blob Storage implementation.

**Constructor**:
```python
AZBlobStorageNylasGrantBackend(
    connection_str: str,
    container_name: str,
    blob_name_pattern: str,
    from_email: str
)
```

**Parameters**:
- `connection_str`: Azure Storage connection string
- `container_name`: Blob container name
- `blob_name_pattern`: Blob filename pattern (e.g., 'nylas_grant.json')
- `from_email`: Email address for generating unique blob path

## Future Enhancements

Potential future improvements:

1. **Additional Backends**: FileSystem, AWS S3, Google Cloud Storage
2. **Grant Refresh**: Automatic token refresh workflows
3. **Versioning**: Track grant history
4. **Encryption**: Additional client-side encryption layer
5. **Caching**: In-memory cache for frequently accessed grants
6. **Monitoring**: Built-in grant health monitoring

## Support

For issues or questions:
- Review this documentation
- Check error logs
- Review Azure Blob Storage documentation
- Consult Nylas API documentation
- File an issue on the project repository

## Changelog

### Version 1.0 (2026-01-15)
- Initial implementation of `AZBlobStorageNylasGrantBackend`
- Migration command `migrate_nylas_grants_to_blob`
- Deprecation of `NylasGrant` database model
- Integration with Mailbox, NylasConnection, NylasTransport, NylasBackend
- Documentation and examples
