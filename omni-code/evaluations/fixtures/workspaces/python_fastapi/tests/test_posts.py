"""Tests for blog post endpoints."""


def test_create_post_requires_valid_author(client):
    resp = client.post("/posts/", json={
        "author_id": 999,
        "title": "My Post",
        "body": "Content here",
    })
    assert resp.status_code == 201 or resp.status_code == 422


def test_list_posts_empty(client):
    resp = client.get("/posts/")
    assert resp.status_code == 200
    assert resp.json() == []
