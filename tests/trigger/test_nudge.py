"""Test customer nudge: language detection + message rendering."""
from zd_agent.trigger.nudge import detect_language, nudge_message


def test_detect_language_chinese():
    assert detect_language("张三") == "zh"
    assert detect_language("凌风") == "zh"


def test_detect_language_japanese_kana():
    assert detect_language("田中たろう") == "ja"
    assert detect_language("カナダ") == "ja"
    assert detect_language("abiru しょうた") == "ja"


def test_detect_language_pure_english_or_latin():
    assert detect_language("abiru shouta") == "en"
    assert detect_language("Razmik") == "en"
    assert detect_language("José Pereira") == "en"


def test_detect_language_blank_fallback_english():
    assert detect_language("") == "en"
    assert detect_language(None) == "en"
    assert detect_language("   ") == "en"


def test_nudge_message_returns_lang_and_body():
    lang, body = nudge_message(customer_name="张三")
    assert lang == "en"
    assert "let us know what you need help with" in body
    assert "billing/refund" in body
    assert "feature usage or how-to guidance" in body
    assert "account/subscription changes" in body
    assert "workflows" in body
    assert "knowledge base" in body
    assert "API" in body
    assert "error/bug" in body
    assert "退款" not in body


def test_nudge_message_japanese_name_still_uses_english_text():
    lang, body = nudge_message(customer_name="田中たろう")
    assert lang == "en"
    assert "let us know what you need help with" in body
    assert "サポート" not in body


def test_nudge_message_english_default():
    lang, body = nudge_message(customer_name=None)
    assert lang == "en"
    assert "Hi," in body
    assert "let us know what you need help with" in body
    assert "billing/refund" in body
    assert "feature usage or how-to guidance" in body
