"""Tests for comment endpoints."""

from fastapi.testclient import TestClient


def test_list_comments_for_post(client):
    resp = client.get("/comments/post/1")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_comment_on_missing_post(client):
    resp = client.post("/comments/", json={
        "post_id": 999,
        "author_id": 1,
        "body": "Nice post!",
    })
    assert resp.status_code == 201 or resp.status_code == 422


def test_delete_nonexistent_comment(client):
    resp = client.delete("/comments/999")
    assert resp.status_code == 200


def test_comment_body_in_response():
    """Verify comment body is returned correctly.

    BUG: This test creates its own TestClient without setting APP_ENV=testing,
    so the auth middleware blocks the request with 401.
    """
    from src.main import app
    manual_client = TestClient(app)
    resp = manual_client.post("/comments/", json={
        "post_id": 1,
        "author_id": 1,
        "body": "Test comment",
    })
    assert resp.status_code == 201
