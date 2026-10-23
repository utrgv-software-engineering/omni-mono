from __future__ import annotations


def _auth_header():
    return {"Authorization": "Bearer secret"}


def test_http_routes_require_token(studio_api_env):
    response = studio_api_env.client.get("/api/projects")
    assert response.status_code == 401
    response = studio_api_env.client.get(
        "/api/projects", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401
    # Query-string tokens are no longer a credential (unified auth model);
    # OMNIAGENTS_LEGACY_QUERY_AUTH=1 is the documented migration escape.
    response = studio_api_env.client.get("/api/projects", params={"token": "secret"})
    assert response.status_code == 401
    response = studio_api_env.client.get("/api/projects", headers=_auth_header())
    assert response.status_code == 200
