import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fitness_tracker.database import Base, get_db
from fitness_tracker.main import app

SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_exercise(client):
    response = client.post(
        "/api/v1/exercises/",
        json={"name": "Bench Press", "muscle_group": "chest", "description": "Flat bench"},
    )
    return response.json()


@pytest.fixture
def sample_workout(client):
    response = client.post(
        "/api/v1/workouts/",
        json={"title": "Morning Workout", "workout_date": "2025-01-15", "notes": "Felt good"},
    )
    return response.json()
