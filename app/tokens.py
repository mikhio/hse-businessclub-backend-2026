import base64
import hashlib
import hmac
import uuid


class InvalidPass(ValueError):
    pass


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class PassSigner:
    """Versioned, event-bound HMAC-SHA256 bearer token; no Telegram IDs inside."""

    def __init__(self, key: str, event_id: str):
        if len(key) < 32:
            raise ValueError("Signing key must contain at least 32 characters")
        self.key = key.encode()
        self.event_id = event_id

    def issue(self, pass_id: uuid.UUID) -> str:
        payload = _encode(f"{self.event_id}:{pass_id}".encode())
        signed = f"hse1.{payload}"
        return f"{signed}.{_encode(hmac.digest(self.key, signed.encode(), hashlib.sha256))}"

    def verify(self, token: str) -> uuid.UUID:
        if not isinstance(token, str) or not 10 <= len(token) <= 300:
            raise InvalidPass("Некорректный пропуск")
        try:
            version, payload, signature = token.split(".")
            signed = f"{version}.{payload}"
            expected = _encode(hmac.digest(self.key, signed.encode(), hashlib.sha256))
            if version != "hse1" or not hmac.compare_digest(signature, expected):
                raise InvalidPass("Подпись пропуска недействительна")
            decoded = base64.b64decode(
                payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
            )
            event, pass_id = decoded.decode().split(":", 1)
            if event != self.event_id:
                raise InvalidPass("Пропуск другого мероприятия")
            return uuid.UUID(pass_id)
        except (ValueError, UnicodeError, TypeError) as exc:
            if isinstance(exc, InvalidPass):
                raise
            raise InvalidPass("Некорректный пропуск") from exc
