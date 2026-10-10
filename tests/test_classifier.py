from app.core.enums import MessageClass
from app.services.message_classifier import classifier


def test_new_signal_classification():
    text = "RLC/USDT\nBUY 0.41-0.43\nTP1 0.44 TP2 0.45\nSL 0.40"
    result = classifier().classify(text)
    assert result.message_class == MessageClass.NEW_SIGNAL
    assert result.confidence >= 0.8


def test_result_does_not_become_signal():
    text = "RLC/USDT TP1 HIT ✅ great profit"
    result = classifier().classify(text)
    assert result.message_class == MessageClass.RESULT_UPDATE


def test_admin_message_ignored():
    result = classifier().classify("Join VIP subscription now - contact admin")
    assert result.message_class == MessageClass.ADMIN
