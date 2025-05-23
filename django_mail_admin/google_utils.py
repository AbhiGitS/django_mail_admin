import base64
import logging

import requests
from django.conf import settings
from social_django.models import UserSocialAuth

# FIXME: Warning - this code was taken from django_mailbox and haven't been touched yet as of 27.11.2017

logger = logging.getLogger(__name__)


class AccessTokenNotFound(Exception):
    pass


class RefreshTokenNotFound(Exception):
    pass


def get_google_consumer_key():
    return settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY


def get_google_consumer_secret():
    return settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET


def get_google_access_token(email):
    # TODO: This should be cacheable
    try:
        me = UserSocialAuth.objects.get(uid=email, provider="google-oauth2")
        return me.extra_data["access_token"]
    except (UserSocialAuth.DoesNotExist, KeyError):
        raise AccessTokenNotFound


def update_google_extra_data(email_or_auth_uid: str, extra_data) -> UserSocialAuth:
    try:
        me = UserSocialAuth.objects.get(uid=email_or_auth_uid, provider="google-oauth2")
        me.extra_data = extra_data
        me.save()
        return me
    except (UserSocialAuth.DoesNotExist, KeyError):
        raise AccessTokenNotFound


def get_google_refresh_token(email):
    try:
        me = UserSocialAuth.objects.get(uid=email, provider="google-oauth2")
        return me.extra_data["refresh_token"]
    except (UserSocialAuth.DoesNotExist, KeyError):
        raise RefreshTokenNotFound


def google_api_get(email, url):
    headers = dict(
        Authorization="Bearer %s" % get_google_access_token(email),
    )
    r = requests.get(url, headers=headers)
    logger.info("google_api_get got a %s", r.status_code)
    if r.status_code == 401:
        # Go use the refresh token
        refresh_authorization(email)
        return google_api_get(email, url)
    if r.status_code == 200:
        try:
            return r.json()
        except ValueError:
            return r.text


def google_api_post(email, url, post_data, authorized=True):
    # TODO: Make this a lot less ugly. especially the 401 handling
    headers = dict()
    if authorized is True:
        headers.update(
            dict(
                Authorization="Bearer %s" % get_google_access_token(email),
            )
        )
    r = requests.post(url, headers=headers, data=post_data)
    logger.info(f"google_api_post got a {r.status_code}, url: {url}, authorized?: {authorized}")
    if r.status_code == 401:
        refresh_authorization(email)
        r = requests.post(url, headers=headers, data=post_data)
    if r.status_code == 200:
        try:
            return r.json()
        except ValueError:
            return r.text
    else:
        logger.error(f"google_api_post ended with a {r.status_code}, url: {url}, authorized?: {authorized}")
        raise Exception("google_api_post ended with a %s" % r.status_code)


def refresh_authorization(email: str) -> UserSocialAuth:
    refresh_token = get_google_refresh_token(email)
    post_data = dict(
        refresh_token=refresh_token,
        client_id=get_google_consumer_key(),
        client_secret=get_google_consumer_secret(),
        grant_type="refresh_token",
    )
    results = google_api_post(
        email,
        "https://oauth2.googleapis.com/token",
        post_data,
        authorized=False,
    )
    results.update({"refresh_token": refresh_token})
    return update_google_extra_data(email, results)


def fetch_user_info(email):
    result = google_api_get(
        email, "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
    )
    return result


def generate_oauth2_string(username, access_token, base64_encode=True):
    """Generates an IMAP OAuth2 authentication string.

    See https://developers.google.com/google-apps/gmail/oauth2_overview

    Args:
      username: the username (email address) of the account to authenticate
      access_token: An OAuth2 access token.
      base64_encode: Whether to base64-encode the output.

    Returns:
      The SASL argument for the OAuth2 mechanism.
    """
    auth_string = "user=%s\1auth=Bearer %s\1\1" % (username, access_token)
    if base64_encode:
        auth_string = base64.b64encode(auth_string.encode("utf-8"))
    return auth_string
