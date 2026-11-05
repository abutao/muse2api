"""Mail material client — Outlook IMAP (password / XOAUTH2) + Microsoft Graph.

Mirrors the working approach from the firefly reference project
(internal/mail/*.go): try IMAP first, fall back to Graph, refresh the
refresh_token against both the consumers and live.com endpoints, and rotate the
refresh_token when Microsoft issues a new one.

Used by the signup machine to pull Muse OTP / recovery codes automatically so a
registration can run end-to-end without a human.
"""

from app.mail import client as client  # noqa: F401
