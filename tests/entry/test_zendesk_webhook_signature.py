import base64
import hashlib
import hmac
from unittest.mock import patch

from zd_agent.services.zendesk_webhook import verify_zendesk_signature


def _sign(secret: str, ts: str, body: bytes) -> str:
    mac = hmac.new(
        secret.encode("utf-8"),
        ts.encode("utf-8") + body,
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def test_verify_valid_signature():
    secret = "topsecret"
    ts = "1700000000"
    body = b'{"event":"test"}'
    sig = _sign(secret, ts, body)
    with patch("zd_agent.services.zendesk_webhook.time.time", return_value=1700000000):
        assert (
            verify_zendesk_signature(
                secret=secret,
                timestamp=ts,
                body=body,
                signature=sig,
            )
            is True
        )


def test_verify_invalid_signature():
    assert (
        verify_zendesk_signature(
            secret="topsecret",
            timestamp="1700000000",
            body=b"{}",
            signature="notasig",
        )
        is False
    )


def test_verify_rejects_empty_secret():
    assert (
        verify_zendesk_signature(
            secret="",
            timestamp="x",
            body=b"{}",
            signature="y",
        )
        is False
    )


def test_verify_rejects_empty_timestamp():
    assert (
        verify_zendesk_signature(
            secret="s",
            timestamp="",
            body=b"{}",
            signature="y",
        )
        is False
    )


def test_verify_different_body_different_signature():
    secret = "x"
    ts = "1"
    sig_for_a = _sign(secret, ts, b"A")
    # Same secret/ts but body 'B' should fail verification with sig_for_a
    assert (
        verify_zendesk_signature(
            secret=secret,
            timestamp=ts,
            body=b"B",
            signature=sig_for_a,
        )
        is False
    )


def test_verify_rejects_signature_older_than_five_minutes():
    secret = "topsecret"
    ts = "1700000000"
    body = b"{}"
    signature = _sign(secret, ts, body)

    with patch("zd_agent.services.zendesk_webhook.time.time", return_value=1700000301):
        result = verify_zendesk_signature(
            secret=secret,
            timestamp=ts,
            body=body,
            signature=signature,
        )

    assert result is False


def test_verify_accepts_fresh_iso_timestamp():
    secret = "topsecret"
    ts = "2023-11-14T22:13:20Z"
    body = b"{}"
    signature = _sign(secret, ts, body)

    with patch("zd_agent.services.zendesk_webhook.time.time", return_value=1700000000):
        result = verify_zendesk_signature(
            secret=secret,
            timestamp=ts,
            body=body,
            signature=signature,
        )

    assert result is True


def test_verify_rejects_signed_invalid_timestamp():
    secret = "topsecret"
    ts = "not-a-timestamp"
    body = b"{}"

    assert (
        verify_zendesk_signature(
            secret=secret,
            timestamp=ts,
            body=body,
            signature=_sign(secret, ts, body),
        )
        is False
    )
