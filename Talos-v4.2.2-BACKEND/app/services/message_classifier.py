from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.core.enums import MessageClass


@dataclass(slots=True)
class Classification:
    message_class: MessageClass
    confidence: float
    reasons: list[str]


_SYMBOL = re.compile(r"\b[A-Z0-9]{2,12}\s*[/|\-]\s*(USDT|USDC|BTC|ETH|USD)\b", re.I)
_SIDE = re.compile(r"\b(BUY|SELL|LONG|SHORT)\b|(?:شراء|بيع|دخول|ادخل|ندخل)", re.I)
_ENTRY = re.compile(r"\b(ENTRY|BUY\s*ZONE|SELL\s*ZONE)\b|(?:دخول|منطقة\s*الدخول)", re.I)
_TARGET = re.compile(r"\b(TP\s*\d*|TARGETS?|TAKE\s*PROFIT)\b|(?:هدف|اهداف|الأهداف|الاهداف)", re.I)
_STOP = re.compile(r"\b(SL|STOP|STOP\s*LOSS)\b|(?:وقف|ستوب)", re.I)
_RESULT = re.compile(
    r"\b(TP\s*\d*\s*(HIT|DONE|REACHED)|TARGET\s*(HIT|DONE|REACHED)|STOP\s*(HIT|LOSS)|SL\s*(HIT|DONE)|CLOSED?\s*(IN\s*)?(PROFIT|LOSS)|PROFIT\s*BOOKED)\b"
    r"|(?:تم\s*(?:تحقيق|الوصول)|ضرب\s*(?:الهدف|الستوب)|حققنا|ربح|خسارة|تم\s*الاغلاق)",
    re.I,
)
_UPDATE = re.compile(
    r"\b(MOVE\s*(SL|STOP)|BREAKEVEN|BREAK\s*EVEN|BE\b|CANCEL\s*(ORDER|PENDING)|UPDATE\s*(SL|STOP|TP)|CLOSE\s*(HALF|\d+%))\b"
    r"|(?:حرك\s*(?:الوقف|الستوب)|نقل\s*(?:الوقف|الستوب)|تعادل|الغاء\s*الامر|إلغاء\s*الأمر|اغلق\s*النصف|أغلق\s*النصف|تحديث\s*(?:الوقف|الاهداف))",
    re.I,
)
_ADMIN = re.compile(
    r"\b(VIP|SUBSCRIPTION|SUBSCRIBE|JOIN\s*US|PREMIUM|PAYMENT|CONTACT\s*ADMIN|PROMO|DISCOUNT)\b"
    r"|(?:اشتراك|اشترك|خاص|مدفوع|تواصل\s*مع\s*الادمن|خصم|عرض\s*خاص)",
    re.I,
)
_NEWS = re.compile(
    r"\b(BREAKING|NEWS|ANNOUNCEMENT|LISTING|DELISTING|SEC|ETF|HACKED|PARTNERSHIP|MAINNET|AIRDROP)\b"
    r"|(?:عاجل|خبر|اخبار|إدراج|ادراج|حذف\s*عملة|شراكة)",
    re.I,
)
_COMMENTARY = re.compile(
    r"\b(BULLISH|BEARISH|MARKET\s*LOOKS|EXPECT|WATCHING|SUPPORT|RESISTANCE|ANALYSIS|DOMINANCE|TREND)\b"
    r"|(?:السوق|تحليل|اتوقع|نتوقع|مقاومة|دعم|ترند|صاعد|هابط)",
    re.I,
)


# Lightweight, deterministic seed lexicon. The API is deliberately model-like so a
# fine-tuned multilingual classifier can replace this component without touching the
# parser, backtester, dashboard, or execution layers.
class HybridMessageClassifier:
    def classify(self, text: str | None, *, has_media: bool = False) -> Classification:
        clean = (text or "").strip()
        if not clean:
            return Classification(
                MessageClass.MEDIA_ONLY if has_media else MessageClass.CHATTER,
                0.99,
                ["no_text"],
            )

        reasons: list[str] = []
        scores: dict[MessageClass, float] = {c: 0.0 for c in MessageClass}

        if _RESULT.search(clean):
            scores[MessageClass.RESULT_UPDATE] += 0.95
            reasons.append("result_language")
        if _UPDATE.search(clean):
            scores[MessageClass.SIGNAL_UPDATE] += 0.95
            reasons.append("trade_update_language")
        if _ADMIN.search(clean):
            scores[MessageClass.ADMIN] += 0.90
            reasons.append("admin_or_promo_language")
        if _NEWS.search(clean):
            scores[MessageClass.NEWS] += 0.65
            reasons.append("news_language")
        if _COMMENTARY.search(clean):
            scores[MessageClass.MARKET_COMMENTARY] += 0.50
            reasons.append("commentary_language")

        structure = 0
        if _SYMBOL.search(clean):
            structure += 1
            reasons.append("symbol")
        if _SIDE.search(clean):
            structure += 1
            reasons.append("direction")
        if _ENTRY.search(clean) or re.search(r"\d+(?:[.,]\d+)?\s*[-–]\s*\d+(?:[.,]\d+)?", clean):
            structure += 1
            reasons.append("entry_structure")
        if _TARGET.search(clean):
            structure += 1
            reasons.append("targets")
        if _STOP.search(clean):
            structure += 1
            reasons.append("stop")

        # Requiring several independent structural clues prevents phrases such as
        # "we are not buying BTC" from being promoted to a signal.
        if structure >= 4:
            scores[MessageClass.NEW_SIGNAL] += 0.98
        elif structure == 3:
            scores[MessageClass.NEW_SIGNAL] += 0.84
        elif structure == 2:
            scores[MessageClass.NEW_SIGNAL] += 0.55

        # Result/update language should beat signal-looking quoted text.
        if scores[MessageClass.RESULT_UPDATE] >= 0.9:
            scores[MessageClass.NEW_SIGNAL] *= 0.35
        if scores[MessageClass.SIGNAL_UPDATE] >= 0.9:
            scores[MessageClass.NEW_SIGNAL] *= 0.45
        if scores[MessageClass.ADMIN] >= 0.9:
            scores[MessageClass.NEW_SIGNAL] *= 0.25

        best_class, best_score = max(scores.items(), key=lambda kv: kv[1])
        if best_score < 0.48:
            if len(clean) < 18:
                return Classification(MessageClass.CHATTER, 0.72, reasons or ["short_unstructured_text"])
            return Classification(MessageClass.UNKNOWN, 0.45, reasons or ["unstructured_text"])

        # Keep probabilities bounded and interpretable.
        return Classification(best_class, round(min(best_score, 0.99), 2), reasons)


@lru_cache(maxsize=1)
def classifier() -> HybridMessageClassifier:
    return HybridMessageClassifier()
