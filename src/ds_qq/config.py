from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_timeout_seconds: float = Field(default=90, gt=0)
    deepseek_max_retries: int = Field(default=2, ge=0, le=5)
    deepseek_max_output_tokens: int = Field(default=2048, ge=1, le=32768)

    onebot_access_token: SecretStr = SecretStr("")
    bot_host: str = "127.0.0.1"
    bot_port: int = Field(default=6099, ge=1, le=65535)
    qq_group_ids: str = ""
    bot_system_prompt: str = "你是群里的 AI 助手，回答简洁、友好、准确。"
    bot_context_messages: int = Field(default=30, ge=1, le=200)
    bot_context_chars: int = Field(default=24000, ge=1000)
    bot_context_images: int = Field(default=4, ge=0, le=20)
    bot_group_cooldown_seconds: float = Field(default=2, ge=0)
    bot_max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)

    data_dir: Path = Path("data")

    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = Field(default=8080, ge=1, le=65535)

    deepseek_price_cache_hit: float = Field(default=0.006, ge=0)
    deepseek_price_cache_miss: float = Field(default=0.3, ge=0)
    deepseek_price_output: float = Field(default=1.2, ge=0)

    @property
    def allowed_group_ids(self) -> frozenset[int]:
        try:
            values = (value.strip() for value in self.qq_group_ids.split(",") if value.strip())
            return frozenset(int(value) for value in values)
        except ValueError as exc:
            message = "QQ_GROUP_IDS must be a comma-separated list of numeric group IDs"
            raise ValueError(message) from exc

    @property
    def database_path(self) -> Path:
        return self.data_dir / "ds_qq.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    def ensure_data_dirs(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def validate_bot(self) -> None:
        if not self.deepseek_api_key.get_secret_value():
            raise ValueError("DEEPSEEK_API_KEY is required")
        if not self.onebot_access_token.get_secret_value():
            raise ValueError("ONEBOT_ACCESS_TOKEN is required")
        if not self.allowed_group_ids:
            raise ValueError("QQ_GROUP_IDS must contain at least one group ID")


@lru_cache
def get_settings() -> Settings:
    return Settings()
