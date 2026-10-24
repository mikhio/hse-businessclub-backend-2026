import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Guest(Base):
    __tablename__ = "guests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80), default="Гость")
    pass_id: Mapped[uuid.UUID] = mapped_column(unique=True, default=uuid.uuid4, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PointEntry(Base):
    __tablename__ = "point_entries"
    __table_args__ = (
        UniqueConstraint("guest_id", "event_id", "reason_code", name="uq_guest_event_reason"),
        CheckConstraint("amount > 0", name="positive_points"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    guest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guests.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(64))
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int]
    reason_code: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
