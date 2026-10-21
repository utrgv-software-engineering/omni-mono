from datetime import date, datetime

from pydantic import BaseModel


# --- Exercise schemas ---


class ExerciseCreate(BaseModel):
    name: str
    muscle_group: str
    description: str | None = None


class ExerciseUpdate(BaseModel):
    name: str | None = None
    muscle_group: str | None = None
    description: str | None = None


class ExerciseResponse(BaseModel):
    id: int
    name: str
    muscle_group: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Workout schemas ---


class WorkoutCreate(BaseModel):
    title: str
    workout_date: date
    notes: str | None = None


class WorkoutUpdate(BaseModel):
    title: str | None = None
    workout_date: date | None = None
    notes: str | None = None


class WorkoutResponse(BaseModel):
    id: int
    title: str
    workout_date: date
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- WorkoutEntry schemas ---


class EntryCreate(BaseModel):
    exercise_id: int
    sets: int
    reps: int
    weight: float | None = None
    notes: str | None = None


class EntryUpdate(BaseModel):
    sets: int | None = None
    reps: int | None = None
    weight: float | None = None
    notes: str | None = None


class EntryResponse(BaseModel):
    id: int
    workout_id: int
    exercise_id: int
    sets: int
    reps: int
    weight: float | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Nested response ---


class WorkoutDetailResponse(WorkoutResponse):
    entries: list[EntryResponse] = []
