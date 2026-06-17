from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Stock AI Lab"
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    database_url: str = "sqlite:///./stock_ai.db"
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    news_api_timeout: float = 10.0
    news_default_display: int = 50
    news_max_display: int = 100
    news_provider: str = "naver"
    news_scheduler_enabled: bool = False
    news_scheduler_interval_minutes: int = 30
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    openai_timeout: float = 30.0
    openai_max_retries: int = 2
    openai_concurrency: int = 1
    security_master_enabled: bool = True
    security_sync_timeout: float = 30.0
    security_sync_max_retries: int = 2
    security_sync_user_agent: str = "StockAILab/1.0"
    sec_user_agent: str | None = None
    nasdaq_listed_url: str = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
    nasdaq_other_listed_url: str = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
    sec_company_tickers_url: str = "https://www.sec.gov/files/company_tickers_exchange.json"
    us_security_minimum_expected_count: int = 1000
    us_security_deactivation_max_ratio: float = 0.10
    krx_api_key: str | None = None
    krx_api_base_url: str = ""
    krx_kospi_basic_api_id: str | None = None
    krx_kosdaq_basic_api_id: str | None = None
    krx_konex_basic_api_id: str | None = None
    krx_etf_daily_api_id: str | None = None
    krx_api_key_param: str = "apiKey"
    krx_api_id_param: str = "serviceId"
    krx_base_date_param: str = "basDd"
    krx_sync_timeout: float = 30.0
    krx_sync_max_retries: int = 2
    krx_business_day_lookback: int = 10
    kr_security_minimum_expected_count: int = 1000
    kr_security_deactivation_max_ratio: float = 0.10
    security_match_min_score: float = 0.75
    security_match_ambiguity_margin: float = 0.08
    news_analysis_batch_size: int = 10
    news_analysis_prompt_version: str = "news-analysis-v1"
    theme_analysis_prompt_version: str = "theme-analysis-v1"
    theme_analysis_window_hours: int = 24
    theme_analysis_max_sources: int = 50
    theme_analysis_min_importance: float = 0.30
    theme_analysis_min_market_relevance: float = 0.30
    theme_analysis_max_themes: int = 3
    recommend_stock_country_scope: str = "KR_ONLY"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("news_api_timeout")
    def _validate_timeout(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 1 or v > 60:
            raise ValueError("news_api_timeout must be between 1 and 60")
        return v

    @field_validator("news_default_display")
    def _validate_default_display(cls, v: int, info) -> int:  # type: ignore[name-defined]
        if v < 1 or v > 100:
            raise ValueError("news_default_display must be between 1 and 100")
        max_d = info.data.get("news_max_display", 100) if hasattr(info, "data") else 100
        if v > max_d:
            raise ValueError("news_default_display cannot be greater than news_max_display")
        return v

    @field_validator("news_max_display")
    def _validate_max_display(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1 or v > 100:
            raise ValueError("news_max_display must be between 1 and 100")
        return v

    @field_validator("news_scheduler_interval_minutes")
    def _validate_scheduler_interval(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 5 or v > 1440:
            raise ValueError("news_scheduler_interval_minutes must be between 5 and 1440")
        return v

    @field_validator("openai_timeout")
    def _validate_openai_timeout(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 5 or v > 120:
            raise ValueError("openai_timeout must be between 5 and 120")
        return v

    @field_validator("openai_max_retries")
    def _validate_openai_retries(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 0 or v > 5:
            raise ValueError("openai_max_retries must be between 0 and 5")
        return v

    @field_validator("openai_concurrency")
    def _validate_openai_concurrency(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1 or v > 5:
            raise ValueError("openai_concurrency must be between 1 and 5")
        return v

    @field_validator("security_sync_timeout")
    def _validate_security_sync_timeout(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 5 or v > 120:
            raise ValueError("security_sync_timeout must be between 5 and 120")
        return v

    @field_validator("security_sync_max_retries")
    def _validate_security_sync_max_retries(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 0 or v > 5:
            raise ValueError("security_sync_max_retries must be between 0 and 5")
        return v

    @field_validator("us_security_minimum_expected_count")
    def _validate_us_security_minimum_expected_count(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1:
            raise ValueError("us_security_minimum_expected_count must be at least 1")
        return v

    @field_validator("us_security_deactivation_max_ratio")
    def _validate_us_security_deactivation_max_ratio(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 0 or v > 1:
            raise ValueError("us_security_deactivation_max_ratio must be between 0 and 1")
        return v

    @field_validator("krx_sync_timeout")
    def _validate_krx_sync_timeout(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 5 or v > 120:
            raise ValueError("krx_sync_timeout must be between 5 and 120")
        return v

    @field_validator("krx_sync_max_retries")
    def _validate_krx_sync_max_retries(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 0 or v > 5:
            raise ValueError("krx_sync_max_retries must be between 0 and 5")
        return v

    @field_validator("krx_business_day_lookback")
    def _validate_krx_business_day_lookback(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1 or v > 30:
            raise ValueError("krx_business_day_lookback must be between 1 and 30")
        return v

    @field_validator("kr_security_minimum_expected_count")
    def _validate_kr_security_minimum_expected_count(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1:
            raise ValueError("kr_security_minimum_expected_count must be at least 1")
        return v

    @field_validator("kr_security_deactivation_max_ratio")
    def _validate_kr_security_deactivation_max_ratio(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 0 or v > 1:
            raise ValueError("kr_security_deactivation_max_ratio must be between 0 and 1")
        return v

    @field_validator("security_match_min_score", "security_match_ambiguity_margin")
    def _validate_security_match_scores(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 0 or v > 1:
            raise ValueError("security match scores must be between 0 and 1")
        return v

    @field_validator("news_analysis_batch_size")
    def _validate_batch_size(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1 or v > 50:
            raise ValueError("news_analysis_batch_size must be between 1 and 50")
        return v

    @field_validator("theme_analysis_window_hours")
    def _validate_theme_window_hours(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1 or v > 168:
            raise ValueError("theme_analysis_window_hours must be between 1 and 168")
        return v

    @field_validator("theme_analysis_max_sources")
    def _validate_theme_max_sources(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 3 or v > 200:
            raise ValueError("theme_analysis_max_sources must be between 3 and 200")
        return v

    @field_validator("theme_analysis_min_importance", "theme_analysis_min_market_relevance")
    def _validate_theme_score_threshold(cls, v: float) -> float:  # type: ignore[name-defined]
        if v < 0 or v > 1:
            raise ValueError("theme score thresholds must be between 0 and 1")
        return v

    @field_validator("theme_analysis_max_themes")
    def _validate_theme_max_themes(cls, v: int) -> int:  # type: ignore[name-defined]
        if v < 1 or v > 3:
            raise ValueError("theme_analysis_max_themes must be between 1 and 3")
        return v

    @field_validator("recommend_stock_country_scope")
    def _validate_recommend_stock_country_scope(cls, v: str) -> str:  # type: ignore[name-defined]
        value = v.upper()
        if value not in {"KR_ONLY", "KR_AND_US", "US_ONLY"}:
            raise ValueError("recommend_stock_country_scope must be KR_ONLY, KR_AND_US, or US_ONLY")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
