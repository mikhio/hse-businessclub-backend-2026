from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.auth import actor_headers
from app.models import Guest, PointEntry
from app.qr import decode_qr
from app.service import GuestService

pytestmark = pytest.mark.database


def register(client, telegram_id=1001):
    response = client.post(
        "/v1/guests/register", json={"telegram_id": telegram_id, "name": "Тестовый гость"}
    )
    assert response.status_code == 200
    return response.json()


def test_api_requires_authentication(client):
    response = client.post(
        "/v1/guests/register",
        headers={"X-API-Key": "wrong"},
        json={"telegram_id": 1001, "name": "Тестовый гость"},
    )
    assert response.status_code == 401


def test_full_scenario_qr_photo_ledger_and_duplicate(client, settings):
    guest = register(client)
    assert register(client)["id"] == guest["id"]
    image = client.get("/v1/guests/1001/qr")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    token = decode_qr(image.content)
    client.headers.update(actor_headers(settings.api_key, 9001))
    scan = client.post(
        "/v1/organizer/scan-photo",
        data={"organizer_id": 9001},
        files={"image": ("photo.png", image.content, "image/png")},
    )
    assert scan.status_code == 200
    assert scan.json()["points"] == 0
    assert scan.json()["checked_in"] is False
    path = f"/v1/organizer/guests/{guest['id']}/check-in"
    first = client.post(path, json={"organizer_id": 9001}).json()
    assert first["awarded"] is True
    assert first["guest"]["checked_in"] is True
    assert first["guest"]["points"] == settings.checkin_points
    entry = first["guest"]["history"][0]
    assert entry["actor_telegram_id"] == 9001
    assert entry["reason"] == "Приход на мероприятие"
    assert entry["occurred_at"]
    client.headers.update(actor_headers(settings.api_key, 9002))
    second = client.post(path, json={"organizer_id": 9002}).json()
    assert second["awarded"] is False
    assert second["guest"]["points"] == settings.checkin_points
    assert len(second["guest"]["history"]) == 1
    assert (
        client.post("/v1/organizer/scan", json={"organizer_id": 9002, "token": token}).json()[
            "checked_in"
        ]
        is True
    )


def test_untrusted_organizer_cannot_scan_photo_or_award(client):
    guest = register(client)
    token = client.get("/v1/guests/1001/pass").json()["token"]
    assert (
        client.post("/v1/organizer/scan", json={"organizer_id": 1001, "token": token}).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/organizer/scan-photo",
            data={"organizer_id": 1001},
            files={"image": ("bad.png", b"not-an-image", "image/png")},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/organizer/guests/{guest['id']}/check-in", json={"organizer_id": 1001}
        ).status_code
        == 403
    )


def test_tampered_token_and_missing_guest(client, settings):
    register(client)
    token = client.get("/v1/guests/1001/pass").json()["token"]
    client.headers.update(actor_headers(settings.api_key, 9999))
    assert client.get("/v1/guests/9999/pass").status_code == 404
    client.headers.update(actor_headers(settings.api_key, 9001))
    assert (
        client.post(
            "/v1/organizer/scan", json={"organizer_id": 9001, "token": token[:-5] + "AAAAA"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/organizer/scan-photo",
            data={"organizer_id": 9001},
            files={"image": ("bad.png", b"not-image", "image/png")},
        ).status_code
        == 400
    )


def test_guest_cannot_spoof_organizer_or_read_another_qr(client, settings):
    guest = register(client)
    token = client.get("/v1/guests/1001/pass").json()["token"]
    assert (
        client.post("/v1/organizer/scan", json={"organizer_id": 9001, "token": token}).status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/organizer/guests/{guest['id']}/check-in", json={"organizer_id": 9001}
        ).status_code
        == 403
    )
    assert client.get("/v1/guests/9001/qr").status_code == 403
    assert (
        client.post(
            "/v1/guests/register", json={"telegram_id": 9001, "name": "Подмена"}
        ).status_code
        == 403
    )
    # Changing the claimed header ID without a matching scoped credential also fails.
    assert (
        client.post(
            "/v1/organizer/scan",
            headers={"X-Telegram-ID": "9001"},
            json={"organizer_id": 9001, "token": token},
        ).status_code
        == 401
    )
    # The master secret itself is never a valid bearer credential.
    assert (
        client.get("/v1/guests/1001/pass", headers={"X-API-Key": settings.api_key}).status_code
        == 401
    )


def test_atomic_checkin_under_concurrent_requests(session_factory, settings):
    with session_factory() as db:
        guest_id = GuestService(db, settings).register(1001, "Тестовый гость").id

    def check_in(index):
        with session_factory() as db:
            return GuestService(db, settings).check_in(9001 + index % 2, guest_id)["awarded"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(check_in, range(16)))
    assert sum(results) == 1
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PointEntry)) == 1
        assert db.scalar(select(func.sum(PointEntry.amount))) == settings.checkin_points


def test_concurrent_registration_creates_one_pass(session_factory, settings):
    def create(_):
        with session_factory() as db:
            guest = GuestService(db, settings).register(1001, "Тестовый гость")
            return guest.id, guest.pass_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(create, range(8)))
    assert len(set(results)) == 1
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Guest)) == 1


def test_ledger_is_per_event(session_factory, settings):
    with session_factory() as db:
        svc = GuestService(db, settings)
        guest = svc.register(1001, "Тестовый гость")
        svc.check_in(9001, guest.id)
        another_settings = settings.model_copy(update={"event_id": "another-event"})
        second = GuestService(db, another_settings)
        assert second.card(guest)["points"] == 0
        assert second.check_in(9001, guest.id)["awarded"] is True
