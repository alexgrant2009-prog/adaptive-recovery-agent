"""Tests for agent.services.token_service — the single-use approval token."""

import time

import pytest

from agent.services import token_service
from agent.services.token_service import ApprovalTokenError

PROPOSAL = {
    "event_id": "evt_w1",
    "action": "replace",
    "replacement": {"title": "Mobility — 15 min", "start": "2026-07-15T17:00:00-04:00",
                    "duration_min": 15, "intensity": "recovery"},
    "rationale": "low HRV + exam tomorrow",
}


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("APPROVAL_TOKEN_SECRET", "unit-test-secret")


@pytest.fixture
def db(tmp_path):
    return tmp_path / "tokens.sqlite"


def test_mint_then_validate_succeeds_once(db):
    token = token_service.mint_token("user-1", PROPOSAL)
    token_service.validate_and_consume(token, "user-1", PROPOSAL, db_path=db)


def test_token_is_single_use(db):
    token = token_service.mint_token("user-1", PROPOSAL)
    token_service.validate_and_consume(token, "user-1", PROPOSAL, db_path=db)
    with pytest.raises(ApprovalTokenError, match="already used"):
        token_service.validate_and_consume(token, "user-1", PROPOSAL, db_path=db)


def test_token_bound_to_proposal_content(db):
    token = token_service.mint_token("user-1", PROPOSAL)
    tampered = {**PROPOSAL, "action": "cancel"}
    with pytest.raises(ApprovalTokenError, match="does not match this proposal"):
        token_service.validate_and_consume(token, "user-1", tampered, db_path=db)


def test_token_bound_to_user(db):
    token = token_service.mint_token("user-1", PROPOSAL)
    with pytest.raises(ApprovalTokenError, match="different user"):
        token_service.validate_and_consume(token, "user-2", PROPOSAL, db_path=db)


def test_expired_token_rejected(db):
    minted_long_ago = time.time() - token_service.TTL_SECONDS - 60
    token = token_service.mint_token("user-1", PROPOSAL, now=minted_long_ago)
    with pytest.raises(ApprovalTokenError, match="expired"):
        token_service.validate_and_consume(token, "user-1", PROPOSAL, db_path=db)


def test_forged_signature_rejected(db):
    token = token_service.mint_token("user-1", PROPOSAL)
    body, sig = token.rsplit(".", 1)
    forged = f"{body}.{'0' * len(sig)}"
    with pytest.raises(ApprovalTokenError, match="signature"):
        token_service.validate_and_consume(forged, "user-1", PROPOSAL, db_path=db)


def test_tampered_body_fails_signature_check(db):
    token = token_service.mint_token("user-1", PROPOSAL)
    body, sig = token.rsplit(".", 1)
    tampered = f"{body}AA.{sig}"
    with pytest.raises(ApprovalTokenError, match="signature"):
        token_service.validate_and_consume(tampered, "user-1", PROPOSAL, db_path=db)


def test_malformed_and_empty_tokens_rejected(db):
    for bad in ["", "no-dot-here", None]:
        with pytest.raises(ApprovalTokenError):
            token_service.validate_and_consume(bad or "", "user-1", PROPOSAL, db_path=db)


def test_missing_secret_refuses_to_operate(monkeypatch, db):
    monkeypatch.delenv("APPROVAL_TOKEN_SECRET")
    with pytest.raises(ApprovalTokenError, match="APPROVAL_TOKEN_SECRET"):
        token_service.mint_token("user-1", PROPOSAL)


def test_proposal_hash_is_key_order_independent():
    reordered = {
        "rationale": PROPOSAL["rationale"],
        "action": PROPOSAL["action"],
        "replacement": dict(reversed(list(PROPOSAL["replacement"].items()))),
        "event_id": PROPOSAL["event_id"],
    }
    assert token_service.proposal_hash(PROPOSAL) == token_service.proposal_hash(reordered)


def test_prune_consumed_removes_old_rows(db):
    token = token_service.mint_token("user-1", PROPOSAL)
    token_service.validate_and_consume(token, "user-1", PROPOSAL, db_path=db)
    assert token_service.prune_consumed(db, older_than_seconds=0) == 1
    assert token_service.prune_consumed(db, older_than_seconds=0) == 0
