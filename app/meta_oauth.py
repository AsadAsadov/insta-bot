"""Phase 2 placeholder for Meta OAuth.

Post listing requires a user OAuth access token and approved permissions
(instagram_basic, pages_show_list, instagram_manage_comments,
instagram_manage_messages) plus App Review before production usage.
"""


def start_login() -> None:
    """TODO: redirect user to Meta OAuth login endpoint."""
    return None


def oauth_callback() -> None:
    """TODO: exchange authorization code for user token and persist session."""
    return None


def fetch_ig_media_list() -> list[dict]:
    """TODO: query Graph API for IG media after OAuth is implemented."""
    return []
