import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Guest, PointEntry
from app.tokens import InvalidPass, PassSigner


class Forbidden(ValueError):
    pass


class NotFound(ValueError):
    pass


class GuestService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.signer = PassSigner(settings.pass_signing_key, settings.event_id)

    def authorize_organizer(self, telegram_id: int) -> None:
        if telegram_id not in self.settings.organizers:
            raise Forbidden("Доступ только организаторам")

    def register(self, telegram_id: int, name: str) -> Guest:
        self.db.execute(
            insert(Guest)
            .values(telegram_id=telegram_id, name=" ".join(name.split()), category="Гость")
            .on_conflict_do_nothing(index_elements=[Guest.telegram_id])
        )
        self.db.commit()
        return self.db.scalar(select(Guest).where(Guest.telegram_id == telegram_id))

    def guest_by_telegram(self, telegram_id: int) -> Guest:
        guest = self.db.scalar(select(Guest).where(Guest.telegram_id == telegram_id))
        if not guest:
            raise NotFound("Сначала зарегистрируйтесь командой /start")
        return guest

    def scan(self, organizer_id: int, token: str) -> Guest:
        self.authorize_organizer(organizer_id)
        pass_id = self.signer.verify(token.strip())
        guest = self.db.scalar(select(Guest).where(Guest.pass_id == pass_id))
        if not guest:
            raise InvalidPass("Пропуск не найден или отозван")
        return guest

    def card(self, guest: Guest) -> dict:
        entries = self.db.scalars(
            select(PointEntry)
            .where(PointEntry.guest_id == guest.id, PointEntry.event_id == self.settings.event_id)
            .order_by(PointEntry.occurred_at, PointEntry.id)
        ).all()
        return {
            "id": str(guest.id),
            "name": guest.name,
            "category": guest.category,
            "event_id": self.settings.event_id,
            "points": sum(entry.amount for entry in entries),
            "checked_in": any(entry.reason_code == "arrival" for entry in entries),
            "history": [
                {
                    "id": str(entry.id),
                    "actor_telegram_id": entry.actor_telegram_id,
                    "amount": entry.amount,
                    "reason": entry.reason,
                    "occurred_at": entry.occurred_at.isoformat(),
                }
                for entry in entries
            ],
        }

    def check_in(self, organizer_id: int, guest_id: uuid.UUID) -> dict:
        self.authorize_organizer(organizer_id)
        guest = self.db.get(Guest, guest_id)
        if not guest:
            raise NotFound("Гость не найден")
        # The uniqueness constraint is the source of truth, even across API workers.
        inserted = self.db.execute(
            insert(PointEntry)
            .values(
                guest_id=guest_id,
                event_id=self.settings.event_id,
                actor_telegram_id=organizer_id,
                amount=self.settings.checkin_points,
                reason_code="arrival",
                reason="Приход на мероприятие",
            )
            .on_conflict_do_nothing(constraint="uq_guest_event_reason")
            .returning(PointEntry.id)
        ).scalar_one_or_none()
        self.db.commit()
        return {"awarded": inserted is not None, "guest": self.card(guest)}
