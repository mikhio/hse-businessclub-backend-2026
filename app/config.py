from functools import cached_property

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://qr:qr@localhost:5432/qr"
    api_key: str = Field(min_length=32)
    pass_signing_key: str = Field(min_length=32)
    organizer_ids: str = ""
    event_id: str = Field(default="hse-businessclub-demo-2026", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    checkin_points: int = Field(default=10, ge=1, le=100000)
    bot_token: str = ""
    backend_url: str = "http://api:8000"

    @field_validator("organizer_ids")
    @classmethod
    def validate_organizer_ids(cls, value: str) -> str:
        for item in value.split(","):
            if item.strip() and (not item.strip().isdigit() or int(item) <= 0):
                raise ValueError("ORGANIZER_IDS must be comma-separated positive Telegram IDs")
        return value

    @cached_property
    def organizers(self) -> frozenset[int]:
        return frozenset(
            int(item.strip()) for item in self.organizer_ids.split(",") if item.strip()
        )
