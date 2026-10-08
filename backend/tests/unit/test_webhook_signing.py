"""FM21 / I17: Standard Webhooks signatures, and the AES-GCM box webhook secrets are stored in."""

import base64
import os

import pytest

from app.config import DEV_WEBHOOK_SECRET_KEYS, Settings
from app.domain.errors import InvalidInput
from app.domain.secretbox import SecretBox
from app.services import webhook_security as ws

# The official vectors: standard-webhooks/libraries/python/tests/test_webhooks.py (test_sign_function,
# test_sign_function_binary_payload), fetched from github.com/standard-webhooks/standard-webhooks.
SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
MSG_ID = "msg_p5jXN8AQM9LWM0D4loKWxJek"
TS = 1614265330


def test_signature_matches_standard_webhooks_vector():
    assert ws.sign(SECRET, MSG_ID, TS, b'{"test": 2432232314}') == (
        "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
    )
    assert ws.sign(SECRET, MSG_ID, TS, bytes(range(256))) == (
        "v1,sEcZ042G8Crqgxbybb3y0eDxQR3TcfYsk11I1XULhSI="
    )
    assert ws.signed_headers(SECRET, MSG_ID, TS, b'{"test": 2432232314}') == {
        "webhook-id": MSG_ID,
        "webhook-timestamp": "1614265330",
        "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=",
        "content-type": "application/json",
    }


def test_verify_refuses_tampering_other_secrets_and_replayed_timestamps():
    body = b'{"test": 2432232314}'
    sig = ws.sign(SECRET, MSG_ID, TS, body)
    assert ws.verify(SECRET, MSG_ID, TS, body, sig, now=TS + 299)
    assert ws.verify(SECRET, MSG_ID, TS, body, f"v1,bm90IGl0 {sig}", now=TS)  # any listed one
    assert not ws.verify(SECRET, MSG_ID, TS, body + b" ", sig, now=TS)
    assert not ws.verify(SECRET, "msg_other", TS, body, sig, now=TS)
    assert not ws.verify(SECRET, MSG_ID, TS + 1, body, sig, now=TS)
    assert not ws.verify(ws.new_secret(), MSG_ID, TS, body, sig, now=TS)
    assert not ws.verify(SECRET, MSG_ID, TS, body, sig.replace("v1,", "v2,"), now=TS)
    # catches: a captured delivery replayed later (or pre-dated) still verifying
    assert not ws.verify(SECRET, MSG_ID, TS, body, sig, now=TS + 301)
    assert not ws.verify(SECRET, MSG_ID, TS, body, sig, now=TS - 301)


def test_new_secret_is_whsec_and_32_random_bytes():
    a, b = ws.new_secret(), ws.new_secret()
    assert a != b and a.startswith("whsec_") and len(base64.b64decode(a[6:])) == 32


def _box(*kids: str) -> SecretBox:
    return SecretBox.parse(
        ",".join(f"{k}:{base64.b64encode(os.urandom(32)).decode()}" for k in kids)
    )


def test_secretbox_is_aes_gcm_bound_to_its_row_and_survives_key_rotation():
    old = _box("k1")
    sealed = old.seal("whsec_x", b"org:sub")
    assert sealed.startswith("k1.") and "whsec_x" not in sealed
    assert old.seal("whsec_x", b"org:sub") != sealed  # a fresh nonce every time
    assert old.open(sealed, b"org:sub") == "whsec_x"
    with pytest.raises(InvalidInput):
        old.open(sealed, b"org:other-sub")  # ciphertext copied onto another row does not open
    raw = bytearray(base64.urlsafe_b64decode(sealed[3:]))
    raw[-1] ^= 1
    with pytest.raises(InvalidInput):
        old.open("k1." + base64.urlsafe_b64encode(bytes(raw)).decode(), b"org:sub")
    # rotation: the last key seals, older ones still open
    rotated = SecretBox.parse(f"{old.spec},k2:{base64.b64encode(os.urandom(32)).decode()}")
    assert rotated.open(sealed, b"org:sub") == "whsec_x"
    assert rotated.seal("whsec_y", b"a").startswith("k2.")
    with pytest.raises(InvalidInput):
        _box("k1").open(sealed, b"org:sub")  # same kid, different key


@pytest.mark.parametrize("spec", ["", "k1", "k1:", "k1:c2hvcnQ=", "bad kid:" + "A" * 44, "k1:!!!"])
def test_secretbox_refuses_unusable_keys(spec):
    with pytest.raises(InvalidInput):
        SecretBox.parse(spec)


def test_settings_refuse_the_dev_webhook_key_on_railway(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RECEIPT_KEYS", "k:" + "r" * 40)
    with pytest.raises(ValueError, match="WEBHOOK_SECRET_KEYS"):
        Settings()
    monkeypatch.setenv("WEBHOOK_SECRET_KEYS", "k:" + base64.b64encode(os.urandom(32)).decode())
    assert Settings().webhook_secret_keys != DEV_WEBHOOK_SECRET_KEYS
    monkeypatch.setenv("WEBHOOK_SECRET_KEYS", "k:short")
    with pytest.raises(ValueError):
        Settings()
