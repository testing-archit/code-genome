import base64
import os
from collections.abc import Generator

os.environ["CODE_GENOME_DATABASE_URL"] = "sqlite:///./test_code_genome.db"
os.environ["CODE_GENOME_JOB_BACKEND"] = "manual"
os.environ["CODE_GENOME_JOB_DELAY_SECONDS"] = "0"
os.environ["CODE_GENOME_SEMANTIC_BACKEND"] = "lsa"
os.environ["GEMINI_API_KEY"] = ""
os.environ["CODE_GENOME_CREDENTIAL_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
    bytes(range(32))
).decode()

import pytest
from code_genome_api import main
from code_genome_api.database import Base, get_db
from code_genome_api.main import app
from code_genome_api.rate_limit import FixedWindowLimiter
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session], None, None]:
    # Set CODE_GENOME_TEST_DATABASE_URL (e.g. a throwaway Postgres) to run against the
    # production dialect; the default is a private in-memory SQLite per test.
    url = os.environ.get("CODE_GENOME_TEST_DATABASE_URL")
    engine = (
        create_engine(url, connect_args={"options": "-c timezone=UTC"})
        if url
        else create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    )
    if url:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    # Every test client shares one host, so give each test its own rate-limit window.
    main.request_limiter = FixedWindowLimiter()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_impact_cache() -> None:
    """Tests reuse snapshot IDs across databases; never serve another test's cached history."""
    from code_genome_api.services import impact

    impact._cache.clear()


@pytest.fixture(autouse=True)
def _no_provider_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never call GitHub; a test that needs provider data patches this fetcher."""
    from code_genome_api.services import provider_evidence

    def offline(url: str, token: str | None) -> object:
        raise provider_evidence.ProviderError("GitHub is not reachable in tests.")

    monkeypatch.setattr(provider_evidence, "github_fetch", offline)
