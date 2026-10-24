import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api import create_app
from app.auth import actor_headers
from app.config import Settings
from app.models import Base


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        api_key="test-only-api-key-" + "a" * 32,
        pass_signing_key="test-only-signing-key-" + "b" * 32,
        organizer_ids="9001,9002",
        event_id="test-event",
        checkin_points=10,
    )


@pytest.fixture
def session_factory():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    # Each test owns its random schema. Never truncate an existing database or schema.
    schema = "test_qr_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"}, pool_size=8)
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def app(settings, session_factory):
    return create_app(settings, session_factory)


@pytest.fixture
def client(app, settings):
    with TestClient(app, headers=actor_headers(settings.api_key, 1001)) as client:
        yield client
