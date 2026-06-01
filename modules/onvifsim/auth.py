"""
auth.py — WS-Security UsernameToken middleware for onvifsim.

Supports three modes configured in config.yaml auth.mode:
  none    — no credentials required (WS-Security header ignored)
  basic   — PasswordText: plaintext password comparison
  digest  — PasswordDigest: SHA-1(nonce + created + password), Base64-encoded

GetSystemDateAndTime is always exempt (per ONVIF spec).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from flask import request

from soap_utils import parse_wssecurity, soap_fault

logger = logging.getLogger("onvifsim.auth")

# Operations that are always exempt from authentication
_NO_AUTH_OPERATIONS = {"GetSystemDateAndTime"}


def _validate_digest(password_digest: str, nonce_b64: str, created: str, expected_password: str) -> bool:
    try:
        nonce = base64.b64decode(nonce_b64)
        raw = nonce + created.encode("utf-8") + expected_password.encode("utf-8")
        expected = base64.b64encode(hashlib.sha1(raw).digest()).decode("utf-8")
        return hmac.compare_digest(password_digest, expected)
    except Exception:
        return False


def make_auth_hook(cfg: dict):
    """
    Return a Flask before_request function that enforces the configured auth mode.
    Register it on each SOAP blueprint with:
        blueprint.before_request(make_auth_hook(cfg))
    """
    mode     = cfg.get("auth", {}).get("mode", "none").lower()
    username = cfg.get("auth", {}).get("username", "admin")
    password = cfg.get("auth", {}).get("password", "")

    def _hook():
        client_ip = request.remote_addr or "-"

        # Determine operation name to check exemptions
        from soap_utils import soap_action_from_request
        operation = soap_action_from_request(request)

        if operation in _NO_AUTH_OPERATIONS:
            logger.debug("%-16s %s  [no-auth exempt]", operation, client_ip)
            return  # allow through

        if mode == "none":
            return  # anonymous — allow all

        token = parse_wssecurity(request.data)

        if token is None:
            logger.warning(
                "%-16s %s  Auth FAILED — no WS-Security header (mode=%s requires credentials)",
                operation, client_ip, mode,
            )
            return soap_fault(
                code="s:Sender",
                subcode="ter:NotAuthorized",
                reason="No WS-Security credentials provided",
                status=401,
            )

        if token["username"] != username:
            logger.warning(
                "%-16s %s  Auth FAILED — unknown user '%s'",
                operation, client_ip, token["username"],
            )
            return soap_fault(
                code="s:Sender",
                subcode="ter:NotAuthorized",
                reason=f"Unknown user '{token['username']}'",
                status=401,
            )

        if mode == "basic":
            if token["password"] != password:
                logger.warning(
                    "%-16s %s  Auth FAILED for user '%s' — wrong password (basic)",
                    operation, client_ip, username,
                )
                return soap_fault(
                    code="s:Sender",
                    subcode="ter:NotAuthorized",
                    reason="Invalid credentials",
                    status=401,
                )
            logger.debug("%-16s %s  Basic auth OK for user '%s'", operation, client_ip, username)
            return

        if mode == "digest":
            if token["password_type"] != "PasswordDigest":
                logger.warning(
                    "%-16s %s  Auth FAILED — expected PasswordDigest, got '%s'",
                    operation, client_ip, token["password_type"],
                )
                return soap_fault(
                    code="s:Sender",
                    subcode="ter:NotAuthorized",
                    reason="PasswordDigest required",
                    status=401,
                )
            if not _validate_digest(token["password"], token["nonce"], token["created"], password):
                logger.warning(
                    "%-16s %s  Auth FAILED for user '%s' — digest mismatch (bad password or nonce reuse)",
                    operation, client_ip, username,
                )
                return soap_fault(
                    code="s:Sender",
                    subcode="ter:NotAuthorized",
                    reason="Invalid credentials",
                    status=401,
                )
            logger.debug("%-16s %s  Digest auth OK for user '%s'", operation, client_ip, username)
            return

        # Unknown mode — fail safe
        logger.error("Unknown auth mode '%s' — denying request", mode)
        return soap_fault(
            code="s:Receiver",
            subcode="ter:NotAuthorized",
            reason="Server misconfiguration",
            status=500,
        )

    return _hook
