import copy
import hmac
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.domain.errors import InvalidInput
from app.domain.receipt import KeyRing, Subject, sign, verify
from app.domain.statuses import Status

OLD, NEW = "k1", "k2"
KEYS = KeyRing.parse(f"{OLD}:{'a' * 32},{NEW}:{'b' * 32}")
REQ, U1, U2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
AT = datetime(2026, 1, 15, 12, 30, 45, 123456, tzinfo=UTC)
HASH = "ab" * 32


def subject(**changes) -> Subject:
    base = {
        "request_id": REQ,
        "version": 2,
        "content_hash": HASH,
        "approved_at": AT,
        "approvers": (U1, U2),
    }
    return Subject(**{**base, **changes})


def check(receipt, live=None, status=Status.APPROVED, keys=KEYS) -> bool:
    return verify(receipt, live or subject(), status, keys)


def test_roundtrip_and_shape():
    r = sign(subject(), KEYS)
    assert r["alg"] == "HS256" and r["kid"] == NEW  # the newest key signs
    assert set(r) == {"alg", "kid", "payload", "signature"}
    assert set(r["payload"]) == {
        "request_id",
        "version",
        "content_hash",
        "approved_at",
        "approvers",
    }
    assert check(r)
    assert check(sign(subject(approvers="auto"), KEYS), subject(approvers="auto"))


def test_approver_order_and_timezone_do_not_matter():
    r = sign(subject(approvers=(U2, U1)), KEYS)
    assert check(r, subject(approvers=(U1, U2)))
    local = AT.astimezone(timezone(timedelta(hours=3)))
    assert check(r, subject(approved_at=local))


def _mutations():
    other = uuid.uuid4()
    yield "request_id", lambda r: r["payload"].update(request_id=str(other))
    yield "version", lambda r: r["payload"].update(version=3)
    yield "content_hash", lambda r: r["payload"].update(content_hash="cd" * 32)
    yield "approved_at", lambda r: r["payload"].update(approved_at="2026-01-15T12:30:45.123457Z")
    yield "approver_0", lambda r: r["payload"]["approvers"].__setitem__(0, str(other))
    yield "approver_1", lambda r: r["payload"]["approvers"].__setitem__(1, str(other))
    yield "approver_dropped", lambda r: r["payload"]["approvers"].pop()
    yield "approvers_to_auto", lambda r: r["payload"].update(approvers="auto")
    yield "kid", lambda r: r.update(kid=OLD)
    yield (
        "signature",
        lambda r: r.update(
            signature=("0" if r["signature"][0] != "0" else "1") + r["signature"][1:]
        ),
    )


@pytest.mark.parametrize(("name", "mutate"), list(_mutations()), ids=[n for n, _ in _mutations()])
def test_tamper_each_signed_field(name, mutate):
    r = sign(subject(), KEYS)
    assert check(r)
    tampered = copy.deepcopy(r)
    mutate(tampered)
    assert not check(tampered)  # catches: a field outside the HMAC


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": uuid.uuid4()},
        {"version": 3},
        {"content_hash": "cd" * 32},
        {"approved_at": AT + timedelta(microseconds=1)},
        {"approvers": (U1,)},
        {"approvers": "auto"},
    ],
)
def test_valid_signature_but_live_content_changed_is_invalid(changes):
    r = sign(subject(), KEYS)  # signature is fine for what it attests
    assert not check(r, subject(**changes))  # catches: verifying the receipt against itself


@pytest.mark.parametrize("status", [s for s in Status if s is not Status.APPROVED])
def test_only_an_approved_request_verifies(status):
    assert not check(sign(subject(), KEYS), status=status)


def test_compare_is_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert check(sign(subject(), KEYS))
    assert calls  # catches: a == comparison (timing oracle)


@pytest.mark.parametrize("alg", ["none", "None", "hs256", "HS512", "", None, 256, ["HS256"]])
def test_envelope_alg_other_than_hs256_rejected(alg):
    r = sign(subject(), KEYS)
    r["alg"] = alg
    assert not check(r)  # catches: alg confusion / downgrade to 'none'


def test_kid_is_signed_even_when_two_kids_share_a_secret():
    shared = KeyRing.parse(f"x1:{'s' * 32},x2:{'s' * 32}")
    r = sign(subject(), shared)
    assert r["kid"] == "x2" and check(r, keys=shared)
    r["kid"] = "x1"
    assert not check(r, keys=shared)  # catches: key-id substitution riding on a valid signature


def test_old_kid_still_verifies_newest_signs():
    old_only = KeyRing.parse(f"{OLD}:{'a' * 32}")
    issued_before_rotation = sign(subject(), old_only)
    assert issued_before_rotation["kid"] == OLD
    assert check(issued_before_rotation)  # catches: rotation invalidating every issued receipt
    assert sign(subject(), KEYS)["kid"] == NEW


def test_unknown_kid_is_invalid_not_error():
    r = sign(subject(), KEYS)
    r["kid"] = "zz"
    assert check(r) is False  # catches: verify leaking key ids through a 500
    assert check(sign(subject(), KEYS), keys=KeyRing.parse(f"{OLD}:{'a' * 32}")) is False


@pytest.mark.parametrize(
    "junk",
    [
        None,
        [],
        "x",
        1,
        {},
        {"alg": "HS256"},
        {"alg": "HS256", "kid": NEW, "payload": [], "signature": "00"},
    ],
)
def test_malformed_receipts_are_invalid_not_errors(junk):
    assert check(junk) is False


def test_malformed_variants_of_a_real_receipt_are_invalid():
    good = sign(subject(), KEYS)
    for mutate in (
        lambda r: r["payload"].update(extra="x"),
        lambda r: r["payload"].pop("version"),
        lambda r: r.update(signature="é" * 64),
        lambda r: r.update(signature=123),
        lambda r: r.update(signature="\ud800"),  # lone surrogate: must not raise on encode
        lambda r: r.update(signature=r["signature"].upper()),
        lambda r: r["payload"].update(version=2.0),  # value-equal but not the signed bytes
        lambda r: r.update(signature=r["signature"][:-2]),
        lambda r: r.update(kid=["k2"]),
        lambda r: r["payload"].update(version=True),
        lambda r: r["payload"].update(approved_at=float("nan")),
        lambda r: r["payload"].update(version=10**5000),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        assert check(bad) is False


def test_subject_validates_its_inputs():
    for changes in (
        {"version": True},
        {"version": 0},
        {"content_hash": "XY"},
        {"content_hash": "AB" * 32},
        {"approved_at": datetime(2026, 1, 1)},  # naive
        {"approvers": ()},
        {"approvers": (U1, U1)},
        {"approvers": "manual"},
        {"request_id": "not-a-uuid"},
    ):
        with pytest.raises(InvalidInput):
            subject(**changes)


def test_key_ring_parsing_fails_closed():
    assert KeyRing.parse(f"a:{'x' * 32}").newest == "a"
    for bad in (
        "",
        "nokeyhere",
        "a:short",
        f"a:{'x' * 32},a:{'y' * 32}",
        f":{'x' * 32}",
        f"a:{'x' * 32},",
        f"a:{'x' * 32}\n",  # a trailing newline from an env file is part of the key on one host only
        f"a: {'x' * 32}",
        f"a:{' ' * 32}",
    ):
        with pytest.raises(InvalidInput):
            KeyRing.parse(bad)


def test_hosted_environment_refuses_the_dev_receipt_key(monkeypatch):
    monkeypatch.delenv("RECEIPT_KEYS", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(ValueError):  # catches: forgeable receipts in production
        Settings()
    monkeypatch.setenv("RECEIPT_KEYS", f"prod1:{'z' * 40}")
    assert Settings().receipt_keys.startswith("prod1:")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT")
    monkeypatch.delenv("RECEIPT_KEYS")
    assert Settings().receipt_keys  # local dev works without configuration
