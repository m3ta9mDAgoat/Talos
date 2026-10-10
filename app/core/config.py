from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Talos"
    app_env: str = "development"
    secret_key: str = "change-me"
    database_url: str = "sqlite:///./talos.db"
    redis_url: str = "redis://localhost:6379/0"

    # Dedicated Telegram account. Keep secrets outside Git.
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_name: str = "talos_telegram"
    telegram_session_string: str | None = None

    # Optional exchange API credentials - live trading is disabled by default.
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    okx_api_key: str | None = None
    okx_api_secret: str | None = None
    okx_password: str | None = None
    bybit_api_key: str | None = None
    bybit_api_secret: str | None = None
    bitget_api_key: str | None = None
    bitget_api_secret: str | None = None
    bitget_password: str | None = None
    kucoin_api_key: str | None = None
    kucoin_api_secret: str | None = None
    kucoin_password: str | None = None
    mexc_api_key: str | None = None
    mexc_api_secret: str | None = None

    live_trading_enabled: bool = False
    embedded_job_worker: bool = True
    embedded_live_watcher: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


settings = Settings()
