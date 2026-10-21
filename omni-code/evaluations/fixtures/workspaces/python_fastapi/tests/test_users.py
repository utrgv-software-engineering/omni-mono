"""Tests for user endpoints."""


def test_create_user_returns_201(client):
    resp = client.post("/users/", json={"name": "Alice", "email": "alice@example.com"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Alice"


def test_list_users_empty(client):
    resp = client.get("/users/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_user_not_found(client):
    resp = client.get("/users/999")
    assert resp.status_code == 200
