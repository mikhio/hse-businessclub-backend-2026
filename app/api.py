import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.auth import actor_key
from app.config import Settings
from app.models import Base
from app.qr import MAX_IMAGE_BYTES, InvalidQR, decode_qr, make_qr
from app.service import Forbidden, GuestService, NotFound
from app.tokens import InvalidPass


class Registration(BaseModel):
    telegram_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120, pattern=r".*\S.*")


class OrganizerRequest(BaseModel):
    organizer_id: int = Field(gt=0)


class ScanRequest(OrganizerRequest):
    token: str = Field(min_length=10, max_length=300)


def create_app(settings: Settings | None = None, session_factory=None) -> FastAPI:
    settings = settings or Settings()
    engine = None
    if session_factory is None:
        engine = create_engine(
            settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5
        )
        session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app):
        if engine is not None:
            Base.metadata.create_all(engine)
        yield
        if engine is not None:
            engine.dispose()

    app = FastAPI(title="HSE Guest Pass", version="0.1.0", lifespan=lifespan)

    def authenticate(
        x_api_key: Annotated[str | None, Header()] = None,
        x_telegram_id: Annotated[str | None, Header()] = None,
    ) -> int:
        if not x_telegram_id or not x_telegram_id.isdigit() or int(x_telegram_id) <= 0:
            raise HTTPException(401, "Недействительные учётные данные")
        actor = int(x_telegram_id)
        if not x_api_key or not secrets.compare_digest(
            x_api_key, actor_key(settings.api_key, actor)
        ):
            raise HTTPException(401, "Недействительные учётные данные")
        return actor

    def require_actor(actual: int, requested: int):
        if actual != requested:
            raise Forbidden("Нельзя действовать от имени другого пользователя")

    def service():
        with session_factory() as db:
            yield GuestService(db, settings)

    Service = Annotated[GuestService, Depends(service)]
    Actor = Annotated[int, Depends(authenticate)]

    @app.exception_handler(Forbidden)
    async def forbidden_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(NotFound)
    async def not_found_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(InvalidPass)
    @app.exception_handler(InvalidQR)
    async def invalid_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/health")
    def health():
        with session_factory() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "event_id": settings.event_id}

    @app.post("/v1/guests/register")
    def register(body: Registration, svc: Service, actor: Actor):
        require_actor(actor, body.telegram_id)
        guest = svc.register(body.telegram_id, body.name.strip())
        return {"id": str(guest.id), "name": guest.name, "category": guest.category}

    @app.get("/v1/guests/{telegram_id}/pass")
    def guest_pass(telegram_id: int, svc: Service, actor: Actor):
        require_actor(actor, telegram_id)
        guest = svc.guest_by_telegram(telegram_id)
        return {"token": svc.signer.issue(guest.pass_id), "guest": svc.card(guest)}

    @app.get("/v1/guests/{telegram_id}/qr")
    def guest_qr(telegram_id: int, svc: Service, actor: Actor):
        require_actor(actor, telegram_id)
        guest = svc.guest_by_telegram(telegram_id)
        return Response(make_qr(svc.signer.issue(guest.pass_id)), media_type="image/png")

    @app.post("/v1/organizer/scan")
    def scan(body: ScanRequest, svc: Service, actor: Actor):
        require_actor(actor, body.organizer_id)
        return svc.card(svc.scan(body.organizer_id, body.token))

    @app.post("/v1/organizer/scan-photo")
    def scan_photo(
        svc: Service,
        actor: Actor,
        organizer_id: Annotated[int, Form(gt=0)],
        image: Annotated[UploadFile, File()],
    ):
        require_actor(actor, organizer_id)
        svc.authorize_organizer(organizer_id)
        data = image.file.read(MAX_IMAGE_BYTES + 1)
        token = decode_qr(data)
        return svc.card(svc.scan(organizer_id, token))

    @app.post("/v1/organizer/guests/{guest_id}/check-in")
    def check_in(guest_id: uuid.UUID, body: OrganizerRequest, svc: Service, actor: Actor):
        require_actor(actor, body.organizer_id)
        return svc.check_in(body.organizer_id, guest_id)

    return app
