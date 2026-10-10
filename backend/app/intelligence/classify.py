"""Transparent, deterministic OSINT enrichment; scores denote editorial priority only."""
from __future__ import annotations
import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

EVENT_RULES = (
    ("security_incident", 94, r"\b(?:exploit(?:ed)?|ransomware|hacked|data breach|stolen funds|security breach)\b"),
    ("delisting", 95, r"\b(?:delist(?:ed|ing)?|trading pairs? remov(?:ed|al))\b"),
    ("sanctions", 91, r"\b(?:sanction(?:ed|s)?|embargo|asset freeze)\b"),
    ("policy_decision", 89, r"\b(?:rate (?:hike|cut|decision)|interest rates?|federal funds rate|monetary policy|central bank decision)\b"),
    ("regulatory_action", 86, r"\b(?:indictment|lawsuit|charges against|investigation|enforcement action|sec approves?|regulatory approval)\b"),
    ("supply_disruption", 88, r"\b(?:pipeline shutdown|production halted|supply disruption|oil spill|shipping disruption|port closure)\b"),
    ("listing", 77, r"\b(?:will list|new listing|lists? .* token|spot listing)\b"),
    ("economic_release", 79, r"\b(?:consumer price index|cpi|jobs report|nonfarm payroll|unemployment rate|gdp|inflation data|producer price index)\b"),
    ("earnings", 73, r"\b(?:earnings|quarterly results|revenue guidance|profit warning|10-k|10-q|8-k)\b"),
    ("merger_acquisition", 77, r"\b(?:acquisition|acquires?|merger|takeover)\b"),
    ("outage", 80, r"\b(?:outage|system failure|trading suspended|service disruption)\b"),
    ("geopolitics", 82, r"\b(?:missile|airstrike|invasion|ceasefire|tariff|conflict escalation)\b"),
    ("technology_release", 60, r"\b(?:software release|mainnet|hard fork|security update|version \d+\.\d+)\b"),
)
MARKET_ASSETS = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP", "dogecoin": "DOGE", "tether": "USDT",
    "brent": "BRENT", "crude oil": "WTI", "gold": "XAU", "silver": "XAG", "natural gas": "NATGAS",
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "tesla": "TSLA", "amazon": "AMZN", "meta platforms": "META",
    "s&p 500": "SPX", "nasdaq": "NASDAQ", "euro": "EUR", "japanese yen": "JPY", "us dollar": "USD",
}
KNOWN_TICKERS = {"BTC", "ETH", "SOL", "XRP", "DOGE", "USDT", "ADA", "BNB", "AVAX", "DOT", "LINK", "LTC", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "SPY", "QQQ", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD"}
REGIONS = {"united states": "US", "china": "CN", "european union": "EU", "russia": "RU", "ukraine": "UA", "iran": "IR", "japan": "JP", "india": "IN", "taiwan": "TW", "saudi arabia": "SA"}


def clean_text(value: str, maxlen: int = 1800) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()[:maxlen]


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("Only public HTTP(S) links are supported")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not (k.startswith("utm_") or k.lower() in {"fbclid", "gclid", "ref"})))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))[:1800]


def classify(title: str, summary: str, category: str, reliability: float) -> dict:
    title = clean_text(title, 750)
    summary = clean_text(summary, 1600)
    text = f"{title} {summary[:320]}".lower()
    matches = [(name, priority) for name, priority, rx in EVENT_RULES if re.search(rx, text, re.I)]
    event_type, base = max(matches, key=lambda x: x[1]) if matches else ("general", 53 if category in {"markets", "macro", "geopolitics"} else 43)
    # Higher source reliability changes *ranking*, never the asserted truth of an item.
    importance = round(max(0, min(100, base * .86 + reliability * 13 + (3 if len(matches) > 1 else 0))), 1)
    classification_confidence = 0.85 if matches else 0.35
    symbols = set(re.findall(r"(?<!\w)\$([A-Z]{2,8})(?!\w)", title))
    symbols |= {s for s in KNOWN_TICKERS if re.search(rf"(?<![A-Za-z0-9]){re.escape(s)}(?![A-Za-z0-9])", title)}
    for phrase, symbol in MARKET_ASSETS.items():
        if re.search(r"\b" + re.escape(phrase) + r"\b", text):
            symbols.add(symbol)
    # Unverified cashtags are still labeled as observed mentions, not as supported tradable markets.
    regions = [code for name, code in REGIONS.items() if re.search(r"\b" + re.escape(name) + r"\b", text)]
    stem = unicodedata.normalize("NFKC", title).lower()
    stem = re.sub(r"\b(?:breaking|update|exclusive|just in|live)\b", "", stem)
    stem = re.sub(r"[^\w\s]", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return {
        "event_type": event_type,
        "importance": importance,
        "classification_confidence": classification_confidence,
        "symbols": sorted(symbols)[:20],
        "regions": sorted(set(regions)),
        "entities": [],
        "cluster_key": hashlib.sha256(stem.encode()).hexdigest(),
    }


def utc_date(value: str | None) -> datetime | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime
    try:
        value = value.strip()
        result = parsedate_to_datetime(value) if re.search(r"[A-Za-z]{3},", value) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not result.tzinfo:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
