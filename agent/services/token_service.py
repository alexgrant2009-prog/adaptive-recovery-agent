"""Approval-token service — the code-level half of the human-in-the-loop gate.

When the user taps Approve on a specific proposal, the application calls
mint_token(). The token is:

- **Signed**: HMAC-SHA256 over the payload with APPROVAL_TOKEN_SECRET, so it
  can't be forged or altered.
- **Bound to the proposal**: the payload carries a SHA-256 hash of the
  canonical proposal JSON. Approving proposal A never authorizes proposal B.
- **Bound to the user** and **short-lived** (TTL_SECONDS).
- **Single-use**: validate_and_consume() atomically records the token's nonce
  in a SQLite table with a UNIQUE constraint; a second use fails, so a replayed
  or duplicated apply request is inert.

The apply node in agent/graph.py calls validate_and_consume() before any
calendar write. Because the model never mints tokens, a prompt-injected or
confused model cannot produce a valid one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

TTL_SECONDS = 15 * 60  # user has 15 minutes to act on an Approve tap
DEFAULT_DB_PATH = "approval_tokens.sqlite"


class ApprovalTokenError(PermissionError):
    """Raised when a token is missing, malformed, forged, expired, replayed,
    or bound to a different user/proposal."""


def proposal_hash(proposal: dict) -> str:
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def mint_token(user_id: str, proposal: dict, *, now: float | None = None) -> str:
    """Create a signed, single-use approval token for one specific proposal."""
    payload = {
        "u": user_id,
        "p": proposal_hash(proposal),
        "exp": int(now if now is not None else time.time()) + TTL_SECONDS,
        "n": secrets.token_hex(16),
    }
    body = _b64encode(json.dumps(payload, sort_keys=True).encode())
    return f"{body}.{_sign(body)}"


def validate_and_consume(
    token: str,
    user_id: str,
    proposal: dict,
    *,
    db_path: str | os.PathLike = DEFAULT_DB_PATH,
    now: float | None = None,
) -> None:
    """Verify the token authorizes exactly this user + proposal, then burn it.

    Raises ApprovalTokenError on any failure. Order matters: the signature is
    checked before the payload is parsed or trusted in any way.
    """
    if not token or "." not in token:
        raise ApprovalTokenError("malformed approval token")
    body, signature = token.rsplit(".", 1)

    if not hmac.compare_digest(signature, _sign(body)):
        raise ApprovalTokenError("approval token signature invalid")

    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, UnicodeDecodeError) as e:
        raise ApprovalTokenError("approval token payload undecodable") from e

    if payload.get("exp", 0) < (now if now is not None else time.time()):
        raise ApprovalTokenError("approval token expired")
    if payload.get("u") != user_id:
        raise ApprovalTokenError("approval token issued for a different user")
    if payload.get("p") != proposal_hash(proposal):
        raise ApprovalTokenError("approval token does not match this proposal")

    nonce = payload.get("n")
    if not nonce:
        raise ApprovalTokenError("approval token missing nonce")
    _consume_nonce(nonce, db_path)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------

def _secret() -> bytes:
    try:
        return os.environ["APPROVAL_TOKEN_SECRET"].encode()
    except KeyError:
        raise ApprovalTokenError(
            "APPROVAL_TOKEN_SECRET is not set; refusing to mint or validate tokens"
        ) from None


def _sign(body: str) -> str:
    return hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _consume_nonce(nonce: str, db_path: str | os.PathLike) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS consumed_tokens ("
            "  nonce TEXT PRIMARY KEY,"
            "  consumed_at REAL NOT NULL"
            ")"
        )
        try:
            conn.execute(
                "INSERT INTO consumed_tokens (nonce, consumed_at) VALUES (?, ?)",
                (nonce, time.time()),
            )
        except sqlite3.IntegrityError:
            raise ApprovalTokenError("approval token already used") from None


def prune_consumed(db_path: str | os.PathLike = DEFAULT_DB_PATH,
                   older_than_seconds: float = 7 * 24 * 3600) -> int:
    """Housekeeping: consumed nonces only need to outlive the token TTL; keep a
    week for audit convenience. Returns the number of rows removed."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM consumed_tokens WHERE consumed_at < ?",
            (time.time() - older_than_seconds,),
        )
        return cursor.rowcount
