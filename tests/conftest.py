import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.storage import Storage, get_storage

BUCKET = "test-bucket"
PASSWORD = "supersecret123"


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        secret_key="test-secret-key-with-at-least-32-bytes-ok",
        s3_access_key="test",
        s3_secret_key="test",
        s3_bucket=BUCKET,
        max_upload_size_mb=1,
        database_url="sqlite://",
    )


@pytest.fixture
def s3(settings):
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name=settings.s3_region,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def client(settings, s3):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with session_factory() as db:
            yield db

    storage = Storage(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_storage] = lambda: storage
    yield TestClient(app)  # no `with`: lifespan (create_all / ensure_bucket) is skipped
    app.dependency_overrides.clear()


@pytest.fixture
def make_headers(client):
    def _make(email: str = "user@example.com") -> dict[str, str]:
        r = client.post("/auth/register", json={"email": email, "password": PASSWORD})
        assert r.status_code == 201, r.text
        r = client.post("/auth/login", data={"username": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _make


@pytest.fixture
def headers(make_headers):
    return make_headers()
