# Fitness Tracker API

A Workout/Fitness Tracker REST API built with FastAPI + SQLite. Create exercises, log workouts with sets/reps/weight, and view your history.

## Setup

```bash
# Install dependencies
uv sync

# Start the development server
uv run uvicorn fitness_tracker.main:app --reload

# Seed sample data
uv run python -m fitness_tracker.seed
```

## API Endpoints

### Exercises `/api/v1/exercises`
| Method | Path | Description |
|--------|------|-------------|
| POST | `/` | Create exercise |
| GET | `/` | List exercises (optional `?muscle_group=` filter) |
| GET | `/{id}` | Get exercise |
| PUT | `/{id}` | Update exercise |
| DELETE | `/{id}` | Delete exercise |

### Workouts `/api/v1/workouts`
| Method | Path | Description |
|--------|------|-------------|
| POST | `/` | Create workout |
| GET | `/` | List all workouts |
| GET | `/recent` | Recent workouts (most recent first) |
| GET | `/{id}` | Get workout with entries |
| PUT | `/{id}` | Update workout |
| DELETE | `/{id}` | Delete workout (cascades entries) |

### Entries `/api/v1/workouts/{workout_id}/entries`
| Method | Path | Description |
|--------|------|-------------|
| POST | `/` | Add entry to workout |
| GET | `/` | List entries for workout |
| PUT | `/{entry_id}` | Update entry |
| DELETE | `/{entry_id}` | Delete entry |

## Running Tests

```bash
uv run pytest
uv run pytest -v  # verbose
```

## Interactive API Docs

Start the server and visit http://localhost:8000/docs for Swagger UI.

## Course Exercises

This project is designed for two exercises:

1. **Bugfix workflow** — There's a bug in the app. Use the seed data and explore the API to find it, write a failing test that reproduces it, then fix it.
2. **Feature workflow** — Design and implement an achievement/badge system. Whiteboard the requirements first, then use your coding agent to help build it.
