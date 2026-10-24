import hashlib
import hmac


def actor_key(master_key: str, telegram_id: int) -> str:
    """Scoped credentials: a guest credential cannot authenticate an organizer ID."""
    return hmac.new(
        master_key.encode(), f"telegram-actor:{telegram_id}".encode(), hashlib.sha256
    ).hexdigest()


def actor_headers(master_key: str, telegram_id: int) -> dict[str, str]:
    return {"X-Telegram-ID": str(telegram_id), "X-API-Key": actor_key(master_key, telegram_id)}
