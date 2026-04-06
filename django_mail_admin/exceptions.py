"""
Custom exceptions for Django Mail Admin
"""


class NylasException(Exception):
    """Base exception for Nylas-related errors"""

    pass


class NylasNotAuthenticated(NylasException):
    """Grant not found or not authenticated"""

    pass


class NylasGrantExpired(NylasException):
    """
    Grant exists but requires re-authentication.

    This exception is raised when a Nylas grant has expired and needs
    the user to go through the OAuth flow again.
    """

    def __init__(self, grant_id: str, email: str, reauth_url: str = None):
        self.grant_id = grant_id
        self.email = email
        self.reauth_url = reauth_url
        message = f"Grant {grant_id} for {email} requires re-authentication"
        if reauth_url:
            message += f". Re-authenticate at: {reauth_url}"
        super().__init__(message)


class NylasGrantInvalid(NylasException):
    """
    Grant is invalid and cannot be used.

    This exception is raised when a Nylas grant is invalid for reasons
    other than expiration (e.g., revoked, deleted, or error state).
    """

    def __init__(self, grant_id: str, reason: str):
        self.grant_id = grant_id
        self.reason = reason
        super().__init__(f"Grant {grant_id} is invalid: {reason}")
