import base64
import hashlib
import hmac

from zd_agent.entry.zendesk_webhook import verify_zendesk_signature


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
    assert verify_zendesk_signature(
        secret=secret, timestamp=ts, body=body, signature=sig,
    ) is True


def test_verify_invalid_signature():
    assert verify_zendesk_signature(
        secret="topsecret", timestamp="1700000000",
        body=b"{}", signature="notasig",
    ) is False


def test_verify_rejects_empty_secret():
    assert verify_zendesk_signature(
        secret="", timestamp="x", body=b"{}", signature="y",
    ) is False


def test_verify_rejects_empty_timestamp():
    assert verify_zendesk_signature(
        secret="s", timestamp="", body=b"{}", signature="y",
    ) is False


def test_verify_different_body_different_signature():
    secret = "x"
    ts = "1"
    sig_for_a = _sign(secret, ts, b"A")
    # Same secret/ts but body 'B' should fail verification with sig_for_a
    assert verify_zendesk_signature(
        secret=secret, timestamp=ts, body=b"B", signature=sig_for_a,
    ) is False
