def test_create_entry(client, sample_workout, sample_exercise):
    workout_id = sample_workout["id"]
    exercise_id = sample_exercise["id"]

    response = client.post(
        f"/api/v1/workouts/{workout_id}/entries/",
        json={"exercise_id": exercise_id, "sets": 4, "reps": 8, "weight": 185.0, "notes": "Good form"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["sets"] == 4
    assert data["reps"] == 8
    assert data["weight"] == 185.0
    assert data["workout_id"] == workout_id


def test_create_entry_workout_not_found(client, sample_exercise):
    response = client.post(
        "/api/v1/workouts/999/entries/",
        json={"exercise_id": sample_exercise["id"], "sets": 3, "reps": 10},
    )
    assert response.status_code == 404


def test_create_entry_exercise_not_found(client, sample_workout):
    response = client.post(
        f"/api/v1/workouts/{sample_workout['id']}/entries/",
        json={"exercise_id": 999, "sets": 3, "reps": 10},
    )
    assert response.status_code == 404


def test_list_entries(client, sample_workout, sample_exercise):
    workout_id = sample_workout["id"]
    exercise_id = sample_exercise["id"]

    client.post(
        f"/api/v1/workouts/{workout_id}/entries/",
        json={"exercise_id": exercise_id, "sets": 3, "reps": 10, "weight": 135.0},
    )
    client.post(
        f"/api/v1/workouts/{workout_id}/entries/",
        json={"exercise_id": exercise_id, "sets": 4, "reps": 8, "weight": 155.0},
    )

    response = client.get(f"/api/v1/workouts/{workout_id}/entries/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


def test_update_entry(client, sample_workout, sample_exercise):
    workout_id = sample_workout["id"]
    exercise_id = sample_exercise["id"]

    create_resp = client.post(
        f"/api/v1/workouts/{workout_id}/entries/",
        json={"exercise_id": exercise_id, "sets": 3, "reps": 10, "weight": 135.0},
    )
    entry_id = create_resp.json()["id"]

    response = client.put(
        f"/api/v1/workouts/{workout_id}/entries/{entry_id}",
        json={"weight": 145.0, "reps": 12},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["weight"] == 145.0
    assert data["reps"] == 12
    assert data["sets"] == 3  # unchanged


def test_delete_entry(client, sample_workout, sample_exercise):
    workout_id = sample_workout["id"]
    exercise_id = sample_exercise["id"]

    create_resp = client.post(
        f"/api/v1/workouts/{workout_id}/entries/",
        json={"exercise_id": exercise_id, "sets": 3, "reps": 10},
    )
    entry_id = create_resp.json()["id"]

    response = client.delete(f"/api/v1/workouts/{workout_id}/entries/{entry_id}")
    assert response.status_code == 204

    response = client.get(f"/api/v1/workouts/{workout_id}/entries/")
    assert response.status_code == 200
    assert len(response.json()) == 0


def test_delete_entry_not_found(client, sample_workout):
    response = client.delete(f"/api/v1/workouts/{sample_workout['id']}/entries/999")
    assert response.status_code == 404


def test_cannot_delete_entry_via_wrong_workout(client, sample_exercise):
    """An entry belonging to workout A should not be deletable via workout B's URL."""
    workout_a = client.post(
        "/api/v1/workouts/", json={"title": "Workout A", "workout_date": "2025-01-10"}
    ).json()
    workout_b = client.post(
        "/api/v1/workouts/", json={"title": "Workout B", "workout_date": "2025-01-11"}
    ).json()

    entry = client.post(
        f"/api/v1/workouts/{workout_a['id']}/entries/",
        json={"exercise_id": sample_exercise["id"], "sets": 3, "reps": 10},
    ).json()

    # Try to delete workout A's entry through workout B's URL
    response = client.delete(f"/api/v1/workouts/{workout_b['id']}/entries/{entry['id']}")
    assert response.status_code == 404

    # Entry should still exist on workout A
    response = client.get(f"/api/v1/workouts/{workout_a['id']}/entries/")
    assert len(response.json()) == 1


def test_cannot_update_entry_via_wrong_workout(client, sample_exercise):
    """An entry belonging to workout A should not be updatable via workout B's URL."""
    workout_a = client.post(
        "/api/v1/workouts/", json={"title": "Workout A", "workout_date": "2025-01-10"}
    ).json()
    workout_b = client.post(
        "/api/v1/workouts/", json={"title": "Workout B", "workout_date": "2025-01-11"}
    ).json()

    entry = client.post(
        f"/api/v1/workouts/{workout_a['id']}/entries/",
        json={"exercise_id": sample_exercise["id"], "sets": 3, "reps": 10, "weight": 100.0},
    ).json()

    # Try to update workout A's entry through workout B's URL
    response = client.put(
        f"/api/v1/workouts/{workout_b['id']}/entries/{entry['id']}",
        json={"weight": 999.0},
    )
    assert response.status_code == 404

    # Entry should be unchanged on workout A
    response = client.get(f"/api/v1/workouts/{workout_a['id']}/entries/")
    assert response.json()[0]["weight"] == 100.0


def test_workout_detail_includes_entry_data(client, sample_workout, sample_exercise):
    """The workout detail endpoint should include full entry data in the nested response."""
    workout_id = sample_workout["id"]
    exercise_id = sample_exercise["id"]

    client.post(
        f"/api/v1/workouts/{workout_id}/entries/",
        json={"exercise_id": exercise_id, "sets": 5, "reps": 5, "weight": 225.0, "notes": "PR attempt"},
    )

    response = client.get(f"/api/v1/workouts/{workout_id}")
    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["exercise_id"] == exercise_id
    assert entry["sets"] == 5
    assert entry["reps"] == 5
    assert entry["weight"] == 225.0
    assert entry["notes"] == "PR attempt"
