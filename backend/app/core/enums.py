from enum import StrEnum


class ResearchStage(StrEnum):
    DRAFT = "draft"
    BACKTESTED = "backtested"
    VALIDATED = "validated"
    PAPER = "paper"
    APPROVED = "approved"
    LIVE = "live"


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    FOREX = "forex"
    STOCK = "stock"
    COMMODITY = "commodity"


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    EXIT = "exit"


class SourceType(StrEnum):
    TELEGRAM = "telegram"
    NEWS = "news"
    RSS = "rss"
    MARKET = "market"
    CUSTOM = "custom"


class SourceStatus(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    ERROR = "error"


class SignalStatus(StrEnum):
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class StopTrigger(StrEnum):
    PRICE_TOUCH = "price_touch"
    CANDLE_CLOSE = "candle_close"


class ExecutionMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class MessageClass(StrEnum):
    NEW_SIGNAL = "new_signal"
    SIGNAL_UPDATE = "signal_update"
    RESULT_UPDATE = "result_update"
    MARKET_COMMENTARY = "market_commentary"
    NEWS = "news"
    ADMIN = "admin"
    CHATTER = "chatter"
    MEDIA_ONLY = "media_only"
    UNKNOWN = "unknown"
