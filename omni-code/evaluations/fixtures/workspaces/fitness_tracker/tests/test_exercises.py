def test_create_exercise(client):
    response = client.post(
        "/api/v1/exercises/",
        json={"name": "Squat", "muscle_group": "legs", "description": "Barbell squat"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Squat"
    assert data["muscle_group"] == "legs"
    assert "id" in data


def test_list_exercises(client, sample_exercise):
    response = client.get("/api/v1/exercises/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_list_exercises_filter_by_muscle_group(client):
    client.post("/api/v1/exercises/", json={"name": "Bench Press", "muscle_group": "chest"})
    client.post("/api/v1/exercises/", json={"name": "Squat", "muscle_group": "legs"})

    response = client.get("/api/v1/exercises/", params={"muscle_group": "chest"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["muscle_group"] == "chest"


def test_get_exercise(client, sample_exercise):
    exercise_id = sample_exercise["id"]
    response = client.get(f"/api/v1/exercises/{exercise_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Bench Press"


def test_get_exercise_not_found(client):
    response = client.get("/api/v1/exercises/999")
    assert response.status_code == 404


def test_update_exercise(client, sample_exercise):
    exercise_id = sample_exercise["id"]
    response = client.put(
        f"/api/v1/exercises/{exercise_id}",
        json={"description": "Updated description"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Updated description"
    assert response.json()["name"] == "Bench Press"


def test_delete_exercise(client, sample_exercise):
    exercise_id = sample_exercise["id"]
    response = client.delete(f"/api/v1/exercises/{exercise_id}")
    assert response.status_code == 204

    response = client.get(f"/api/v1/exercises/{exercise_id}")
    assert response.status_code == 404


def test_filter_exercises_nonexistent_group_returns_empty(client, sample_exercise):
    """Filtering by a muscle group with no exercises returns an empty list, not an error."""
    response = client.get("/api/v1/exercises/", params={"muscle_group": "nonexistent"})
    assert response.status_code == 200
    assert response.json() == []


def test_update_exercise_preserves_unchanged_fields(client, sample_exercise):
    """Updating one field should not affect others."""
    exercise_id = sample_exercise["id"]

    response = client.put(
        f"/api/v1/exercises/{exercise_id}",
        json={"description": "New description only"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "New description only"
    assert data["name"] == "Bench Press"
    assert data["muscle_group"] == "chest"


def test_create_exercise_missing_required_fields(client):
    """Creating an exercise without required fields returns 422."""
    response = client.post("/api/v1/exercises/", json={"description": "no name or group"})
    assert response.status_code == 422
