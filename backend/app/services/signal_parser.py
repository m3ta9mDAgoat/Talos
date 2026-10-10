from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from app.core.enums import MarketType, Side, SignalStatus, StopTrigger
from app.schemas.signal import EntrySpec, NormalizedSignal, ParseSignalResponse, StopSpec

# Release 4.7.6. All extractors are deterministic and preserve the verbatim text.
# Regex matching is bounded to structured sections: no arithmetic guesswork and no
# propagation of numeric values from reply previews into the trading instruction.
NUM = r"(?<![\dA-Za-z])(?:\d+(?:[.,]\d+)?)(?![\dA-Za-z])"
F = r"(\d+(?:[.,]\d+)?)"
RANGE_SEP = r"(?:-|–|—|~|\bto\b|الى|إلى|حتى|→)"
SIDE_BUY = re.compile(r"\b(?:BUY|LONG|SPOT\s*BUY|BULLISH\s*ENTRY)\b|شراء|شِراء|دخول\s*شراء", re.I)
SIDE_SELL = re.compile(r"\b(?:SELL|SHORT)\b|بيع|دخول\s*بيع", re.I)
ENTRY_LABEL = re.compile(r"\b(?:buy|entry|entries|entry\s*zone|buy\s*zone|buy\s*price|entry\s*price|buying\s*zone|enter|zone|purchase)\b|(?:شراء|دخول|منطقة\s*الدخول|سعر\s*الدخول|منطقة\s*الشراء|سعر\s*الشراء)", re.I)
TP_LABEL = re.compile(r"\b(?:targets?|take\s*profit|take\s*profits|tps?|profits?|goals?|objectives?|tp\s*\d+|t\s*\d+)\b|(?:الاهداف|الأهداف|هدف|الهدف|الغايات)", re.I)
SL_LABEL = re.compile(r"\b(?:stop(?:\s*loss)?|s\s*\/?\s*l|invalidation)\b|(?:ستوب|وقف|وقف\s*الخسارة|إلغاء\s*الصفقة)", re.I)
CLOSE_LABEL = re.compile(r"\b(?:close|closing|candle\s*close|closed)\b|إغلاق|اغلاق|إقفال|اقفال", re.I)
TIMEFRAME = re.compile(r"(?<!\d)(\d{1,2})\s*(h|hr|hrs|hour|hours|m|min|minute|minutes|d|day|days|ساعات|ساعة|دقائق|دقيقة|أيام|يوم)(?!\w)", re.I)
PAIR = re.compile(r"(?<![A-Za-z0-9])#?([A-Z][A-Z0-9]{1,17})\s*[/|:_-]\s*(USDT|USDC|USD|BTC|ETH|BUSD|FDUSD)(?!\w)", re.I)
SUFFIX_PAIR = re.compile(r"(?<![A-Za-z0-9])#?([A-Z][A-Z0-9]{1,17})(USDT|USDC|BUSD|FDUSD)(?!\w)", re.I)
COIN_LABEL = re.compile(r"(?:coin|token|pair|asset|عملة|العملة|رمز)\s*[:：=]\s*#?\$?([A-Z][A-Z0-9]{1,17})(?!\w)", re.I)
HASH = re.compile(r"(?<![\w])#([A-Z][A-Z0-9]{1,17})(?!\w)", re.I)
NUMBER = re.compile(NUM, re.I)
LIST_LINE = re.compile(rf"^\s*(?:[-•*✔✅🎯↪→]\s*)?(?:\d{{1,2}}\s*[\).:：-]|T\s*\d{{1,2}}\s*[:：).=-]|TP\s*\d{{1,2}}\s*[:：).=-]|هدف\s*\d{{1,2}}\s*[:：).=-])\s*{F}\s*(?:\b|$)", re.I)
TARGET_INDEX = re.compile(rf"(?:\b(?:TP|T|TARGET|GOAL)\s*#?\s*\d{{1,2}}|هدف\s*\d{{1,2}})\s*[:：=@-]?\s*{F}", re.I)


def _normalize(text: str) -> str:
    # Decompose Telegram keycap emojis without deleting digits, translate Arabic
    # digits and punctuation; leave original untouched in NormalizedSignal.raw_text.
    text = unicodedata.normalize('NFKC', text or '').replace('\ufe0f', '').replace('\u20e3', '')
    text = text.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫', '01234567890123456789.'))
    return text.replace('：', ':').replace('،', ',').replace('\r', '\n')


def _primary_message(text: str) -> str:
    # Telegram reply preview is metadata, not an instruction. Do not include
    # markdown-quoted copies or common 'replying to' wrappers in parsing.
    lines = text.splitlines()
    filtered = []
    inside_quote = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(('>', '│', '┃')):
            continue
        if re.match(r"^(?:replying to|quoted message|original message|الرد على|الرسالة المقتبسة)\s*[:：]", stripped, re.I):
            inside_quote = True
            continue
        if inside_quote and re.match(r'^(?:-[- ]{2,}|\[end quote\]|end quote)$', stripped, re.I):
            inside_quote = False
            continue
        if not inside_quote:
            filtered.append(line)
    return '\n'.join(filtered)


def _float(value: str) -> float:
    return float(value.replace(',', '.'))


def _numbers(text: str) -> list[float]:
    return [_float(m.group(0)) for m in NUMBER.finditer(text) if not text[m.end():].lstrip().startswith("%")]


def _first_price(text: str) -> float | None:
    values = _numbers(text)
    return next((n for n in values if n > 0), None)


def _timeframe(text: str) -> str | None:
    m = TIMEFRAME.search(text)
    if not m:
        return None
    unit = m.group(2).lower()
    suffix = 'h' if unit in {'h', 'hr', 'hrs', 'hour', 'hours', 'ساعات', 'ساعة'} else ('d' if unit in {'d', 'day', 'days', 'أيام', 'يوم'} else 'm')
    return f'{m.group(1)}{suffix}'


def _entry(text: str, lines: list[str]) -> EntrySpec | None:
    for i, line in enumerate(lines):
        match = ENTRY_LABEL.search(line)
        if not match:
            continue
        remainder = line[match.end():]
        # Don't use numbers from e.g. symbol names or T1 from other lines.
        fragment = remainder.strip(' :\t=–-🎯⚡')
        if not NUMBER.search(fragment) and i+1 < len(lines):
            fragment = lines[i+1].strip()
        rng = re.search(rf"{F}\s*{RANGE_SEP}\s*{F}", fragment, re.I)
        if rng:
            a, b = _float(rng.group(1)), _float(rng.group(2))
            if a > 0 and b > 0:
                return EntrySpec(type='range', min=min(a, b), max=max(a,b))
        val = _first_price(fragment)
        if val is not None:
            return EntrySpec(type='price', price=val)
        if re.search(r'\b(market|now|current)\b|السوق|الآن|فوري', fragment, re.I):
            return EntrySpec(type='market')
    return None


def _targets(lines: list[str]) -> list[float]:
    values = []
    active = False
    for i, line in enumerate(lines):
        if SL_LABEL.search(line):
            active = False
            continue
        if ENTRY_LABEL.search(line) and not TP_LABEL.search(line):
            active = False
            continue
        labelled = TP_LABEL.search(line)
        if labelled:
            active = True
            # T1 0.34, TP1: 0.34, 'Targets: 0.34 / 0.35', etc
            remainder = line[labelled.end():]
            nums = _numbers(remainder)
            for v in nums:
                if v > 0 and v not in values:
                    values.append(v)
            continue
        if active:
            match = LIST_LINE.search(line)
            if match:
                v = _float(match.group(1))
                if v > 0 and v not in values:
                    values.append(v)
                continue
            plain = line.strip(' \t\u2022-🎯✔✅↪→')
            if re.fullmatch(rf'{F}\s*', plain):
                v = _float(plain)
                if v > 0 and v not in values:
                    values.append(v)
                continue
            if line.strip() and not re.match(r'^(?:\d+[)\].:：]|[-•*])', line.strip()):
                active = False
        # Mixed inline 'TP1 0.12 TP2 0.15'
        for m in TARGET_INDEX.finditer(line):
            v = _float(m.group(1))
            if v > 0 and v not in values:
                values.append(v)
    return values[:20]


def _stop(lines: list[str]) -> tuple[StopSpec | None, list[str]]:
    warnings = []
    for i, line in enumerate(lines):
        match = SL_LABEL.search(line)
        if not match:
            continue
        fragment = line[match.end():].strip(' :\t=-')
        context = ' '.join(lines[i:i+3])[:260]
        # Timeframe numerals are not stop prices: 'SL 4H close under 0.102'.
        stripped = TIMEFRAME.sub(' ', fragment)
        candidate = re.search(rf'(?:below|under|lower\s*than|أقل\s*من|اقل\s*من|تحت)\s*[:：-]?\s*{F}', stripped, re.I)
        value = _float(candidate.group(1)) if candidate else _first_price(stripped)
        if value is None:
            if i+1 < len(lines):
                value = _first_price(lines[i+1])
        if value is None:
            warnings.append('Stop label found without a numerical price')
            return None, warnings
        if CLOSE_LABEL.search(context):
            tf = _timeframe(context)
            if not tf:
                warnings.append('Candle close stop has no unambiguous timeframe')
                return None, warnings
            return StopSpec(price=value, trigger=StopTrigger.CANDLE_CLOSE, timeframe=tf), warnings
        return StopSpec(price=value, trigger=StopTrigger.PRICE_TOUCH), warnings
    return None, ['No stop detected']


class TelegramSignalParser:
    """Conservative, channel-agnostic multi-layout extractor.

    Signal confidence = fraction of five fields observed, NOT expected win rate.
    Only complete geometrically consistent signals are marked PARSED.
    """

    def parse(self, text: str, *, timestamp: datetime | None = None, source_id: str | None = None,
              source_message_id: str | None = None, default_quote: str = 'USDT') -> ParseSignalResponse:
        clean = _primary_message(_normalize(text)).strip()
        lines = [x.strip() for x in clean.splitlines() if x.strip()]
        warnings: list[str] = []
        symbol = None
        for pat in (PAIR, SUFFIX_PAIR):
            m = pat.search(clean)
            if m:
                symbol = f'{m.group(1).upper()}/{m.group(2).upper()}'
                break
        if not symbol:
            m = COIN_LABEL.search(clean) or HASH.search(clean)
            if m:
                symbol = f'{m.group(1).upper()}/{default_quote.upper()}'
        direction = Side.BUY if SIDE_BUY.search(clean) else (Side.SELL if SIDE_SELL.search(clean) else Side.HOLD)
        if not symbol:
            return ParseSignalResponse(signal=None, confidence=0, warnings=['Symbol not found'])
        entry = _entry(clean, lines)
        targets = _targets(lines)
        stop, stop_warnings = _stop(lines)
        warnings.extend(stop_warnings)
        if direction == Side.HOLD:
            warnings.append('Direction not found')
        if entry is None:
            warnings.append('Entry missing')
        if not targets:
            warnings.append('Targets missing')
        # A missing stop is reviewable but never silently converted to a market stop.
        if entry and entry.type != 'market':
            ref = entry.max if entry.type == 'range' else entry.price
            low = entry.min if entry.type == 'range' else entry.price
            if direction == Side.BUY:
                bad = [x for x in targets if x <= float(low)]
                bad_stop = stop is not None and stop.price >= float(ref)
            else:
                bad = [x for x in targets if x >= float(ref)]
                bad_stop = stop is not None and stop.price <= float(low)
            if bad:
                warnings.append(f'Target geometry inconsistent with {direction.value} entry: {bad}')
            if bad_stop:
                warnings.append('Stop geometry inconsistent with entry')
        else:
            bad=[];bad_stop=False
        score = sum([bool(symbol), direction!=Side.HOLD, entry is not None, bool(targets), stop is not None])
        status = SignalStatus.PARSED if score == 5 and not bad and not bad_stop else SignalStatus.NEEDS_REVIEW
        return ParseSignalResponse(signal=NormalizedSignal(
            strategy_id='telegram-copy', strategy_version='hybrid-v4.7.6', source='telegram',
            source_id=source_id, source_message_id=source_message_id,
            timestamp=timestamp or datetime.now(timezone.utc), market_type=MarketType.SPOT,
            symbol=symbol, quote=symbol.split('/')[-1], side=direction,
            entry=entry, stop=stop, targets=targets, confidence=round(score/5,2),
            status=status, raw_text=text,
            metadata={'parser':'adaptive_rules_v4.7.6','field_completeness':round(score/5,2),'warnings':warnings}
        ), confidence=round(score/5,2),warnings=warnings)

telegram_signal_parser = TelegramSignalParser()
