"""Shared test fixtures."""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_db():
    from src import database
    database.reset()
    yield
    database.reset()


@pytest.fixture
def client():
    os.environ["APP_ENV"] = "testing"
    from src.main import app
    with TestClient(app) as c:
        yield c
    os.environ.pop("APP_ENV", None)
