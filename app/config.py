"""Runtime configuration loaded from environment variables and .env.

This module is intentionally tiny: every external provider key lives here so
the rest of the application can depend on a single Settings object instead of
reading environment variables ad hoc.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Core application metadata.
    app_name: str = "ouzi-AI"

    # LLM provider credentials. Empty values are allowed; providers fall back
    # to the local demo channel when the corresponding key is not configured.
    deepseek_api_key: str | None = None
    doubao_api_key: str | None = None
    doubao_model_id: str | None = None
    bailian_api_key: str | None = None

    # Tool-calling configuration. Time tools work locally; search requires one
    # of the optional search provider keys below.
    enable_tools: bool = True
    tavily_api_key: str | None = None
    serpapi_api_key: str | None = None

    # Baidu Intelligent Cloud speech credentials and default synthesis knobs.
    # Baidu calls these API Key and Secret Key in the console.
    baidu_api_key: str | None = None
    baidu_secret_key: str | None = None
    baidu_cuid: str = "aethervoice"
    baidu_asr_rate: int = 16000
    baidu_asr_dev_pid: int = 1537
    baidu_tts_per: int = 0
    baidu_tts_spd: int = 5
    baidu_tts_pit: int = 5
    baidu_tts_vol: int = 5

    # extra="ignore" lets users keep unrelated notes or provider settings in
    # .env without breaking pydantic settings validation.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings so provider clients do not reread .env per call."""
    return Settings()
