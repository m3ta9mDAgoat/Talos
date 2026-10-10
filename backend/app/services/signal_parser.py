from __future__ import annotations

import re
from datetime import datetime, timezone

from app.core.enums import MarketType, Side, SignalStatus, StopTrigger
from app.schemas.signal import EntrySpec, NormalizedSignal, ParseSignalResponse, StopSpec

_NUMBER = r"([0-9]+(?:[.,][0-9]+)?)"


class TelegramSignalParser:
    """Deterministic parser for common Arabic/English crypto signal formats.

    It intentionally prefers 'needs review' over guessing. Channel-specific parsers
    can later subclass/replace this generic parser without changing Talos core.
    """

    _symbol_patterns = [
        re.compile(r"\b([A-Z0-9]{2,20})\s*[|/]\s*([A-Z0-9]{2,12})\b", re.I),
        re.compile(r"(?<![A-Za-z0-9])(?:#|\$)?([A-Z][A-Z0-9]{1,18})\s*[-_ ]?\s*(USDT|USDC|USD|BTC|ETH)\b", re.I),
        re.compile(r"(?:#|\$)([A-Z][A-Z0-9]{1,18})\b", re.I),
    ]
    _buy = re.compile(r"\bBUY\b|\bLONG\b|\bSPOT\b|شراء|دخول|صفقة\s*شراء", re.I)
    _sell = re.compile(r"\bSELL\b|\bSHORT\b|بيع", re.I)

    _entry_range_patterns = [
        re.compile(rf"(?:BUY|ENTRY|ENTER|دخول|شراء)?\s*[:：-]?\s*{_NUMBER}\s*(?:-|–|—|TO|الى|إلى)\s*{_NUMBER}", re.I),
        re.compile(rf"(?:ZONE|من)\s*{_NUMBER}\s*(?:-|–|—|TO|الى|إلى)\s*{_NUMBER}", re.I),
    ]
    _entry_price_patterns = [
        re.compile(rf"(?:ENTRY(?:\s+PRICE)?|ENTER|BUY(?:\s+PRICE)?|دخول|شراء|نقطة\s*الدخول)\s*(?:AT|@|ZONE|PRICE|سعر|عند)?\s*[:：=@-]?\s*{_NUMBER}", re.I),
    ]

    _target_line = re.compile(rf"(?:TP\s*\d*|TARGET(?:\s*#?\d\d?(?=\s*[:：=@-]))?|TAKE\s*PROFIT(?:\s*#?\d\d?(?=\s*[:：=@-]))?|هدف\s*\d*|الهدف\s*\d*|\b\d+\s*:)\s*[:：=@-]?\s*{_NUMBER}", re.I)
    _targets_inline = re.compile(r"(?:TPS?|TARGETS?|TAKE\s*PROFIT|الأهداف|اهداف|أهداف)\s*[:：=-]?\s*([0-9.,\s/|;–-]+)", re.I)

    _stop_price_patterns = [
        re.compile(rf"(?:SL|STOP(?:\s*LOSS)?|وقف)\s*[:：=-]?\s*{_NUMBER}", re.I),
        re.compile(rf"(?:BELOW|UNDER|اقل\s+من|أقل\s+من|تحت)\s*{_NUMBER}", re.I),
    ]
    _candle_close = re.compile(r"(?:CLOSE|CANDLE\s*CLOSE|اغلاق|إغلاق)", re.I)
    _timeframe = re.compile(r"\b(1|2|3|4|6|8|12)\s*(?:H|HR|HOUR|HOURS|ساعة|ساعات)\b", re.I)

    @staticmethod
    def _float(value: str) -> float:
        return float(value.replace(",", "."))

    def parse(
        self,
        text: str,
        *,
        timestamp: datetime | None = None,
        source_id: str | None = None,
        source_message_id: str | None = None,
        default_quote: str = "USDT",
    ) -> ParseSignalResponse:
        cleaned = text.strip()
        upper = cleaned.upper()
        warnings: list[str] = []
        score_parts = 0

        symbol = None
        quote = default_quote.upper()
        for index, pattern in enumerate(self._symbol_patterns):
            match = pattern.search(upper)
            if not match:
                continue
            if index == 0:
                base, quote = match.group(1).upper(), match.group(2).upper()
            else:
                base = match.group(1).upper()
            symbol = f"{base}/{quote}"
            score_parts += 1
            break

        if self._buy.search(cleaned):
            side = Side.BUY
            score_parts += 1
        elif self._sell.search(cleaned):
            side = Side.SELL
            score_parts += 1
        else:
            side = Side.HOLD
            warnings.append("Could not determine BUY/SELL direction.")

        entry: EntrySpec | None = None
        for pattern in self._entry_range_patterns:
            match = pattern.search(cleaned)
            if match:
                a, b = self._float(match.group(1)), self._float(match.group(2))
                entry = EntrySpec(type="range", min=min(a, b), max=max(a, b))
                score_parts += 1
                break
        if entry is None:
            for pattern in self._entry_price_patterns:
                match = pattern.search(cleaned)
                if match:
                    entry = EntrySpec(type="price", price=self._float(match.group(1)))
                    score_parts += 1
                    break
        if entry is None and re.search(r"(?:BUY|ENTER|ENTRY|شراء|دخول).{0,18}(?:NOW|MARKET|سوق|الآن|فوري)|(?:MARKET|NOW).{0,12}(?:BUY|شراء)", cleaned, re.I | re.S):
            entry = EntrySpec(type="market")
            score_parts += 1
        if entry is None:
            warnings.append("Could not determine entry price/zone.")

        targets: list[float] = []
        for match in self._target_line.finditer(cleaned):
            value = self._float(match.group(1))
            if value not in targets:
                targets.append(value)
        if not targets:
            inline = self._targets_inline.search(cleaned)
            if inline:
                for token in re.findall(_NUMBER, inline.group(1)):
                    value = self._float(token)
                    if value not in targets:
                        targets.append(value)
        if targets:
            score_parts += 1
        else:
            warnings.append("No targets detected.")

        stop: StopSpec | None = None
        stop_price = None
        # Restrict search to text around stop/SL when possible so entry numbers do not become stops.
        stop_fragment = cleaned
        stop_anchor = re.search(r"(?:SL|STOP|وقف)", cleaned, re.I)
        if stop_anchor:
            stop_fragment = cleaned[stop_anchor.start():]
        for pattern in self._stop_price_patterns:
            match = pattern.search(stop_fragment)
            if match:
                stop_price = self._float(match.group(1))
                break
        if stop_price is not None:
            candle_close = bool(self._candle_close.search(stop_fragment))
            timeframe = None
            trigger = StopTrigger.PRICE_TOUCH
            if candle_close:
                trigger = StopTrigger.CANDLE_CLOSE
                tf = self._timeframe.search(stop_fragment)
                timeframe = f"{tf.group(1)}h" if tf else None
                if timeframe is None:
                    warnings.append("Stop says candle close but timeframe is unclear.")
            if trigger == StopTrigger.CANDLE_CLOSE and timeframe is None:
                # Preserve the stop price but mark the overall signal for review.
                stop = None
            else:
                stop = StopSpec(price=stop_price, trigger=trigger, timeframe=timeframe)
                score_parts += 1
        else:
            warnings.append("No stop detected.")

        if symbol is None:
            warnings.append("Could not determine trading symbol.")

        # Five principal fields: symbol, direction, entry, targets, stop.
        confidence = round(score_parts / 5, 2)
        hard_missing = symbol is None or side == Side.HOLD or entry is None
        status = SignalStatus.NEEDS_REVIEW if hard_missing or confidence < 0.8 else SignalStatus.PARSED

        if symbol is None:
            return ParseSignalResponse(signal=None, confidence=confidence, warnings=warnings)

        signal = NormalizedSignal(
            source="telegram",
            source_id=source_id,
            source_message_id=source_message_id,
            timestamp=timestamp or datetime.now(timezone.utc),
            market_type=MarketType.SPOT,
            symbol=symbol,
            quote=quote,
            side=side,
            entry=entry,
            stop=stop,
            targets=targets,
            confidence=confidence,
            status=status,
            raw_text=text,
            metadata={"parser": "generic_telegram_v1"},
        )
        return ParseSignalResponse(signal=signal, confidence=confidence, warnings=warnings)


telegram_signal_parser = TelegramSignalParser()
