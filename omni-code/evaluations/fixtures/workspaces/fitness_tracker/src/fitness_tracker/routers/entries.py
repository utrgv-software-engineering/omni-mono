from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fitness_tracker.database import get_db
from fitness_tracker.models import Exercise, Workout, WorkoutEntry
from fitness_tracker.schemas import EntryCreate, EntryResponse, EntryUpdate

router = APIRouter(prefix="/api/v1/workouts/{workout_id}/entries", tags=["entries"])


def _get_workout_or_404(workout_id: int, db: Session) -> Workout:
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    return workout


@router.post("/", response_model=EntryResponse, status_code=201)
def create_entry(
    workout_id: int, entry: EntryCreate, db: Session = Depends(get_db)
):
    _get_workout_or_404(workout_id, db)
    exercise = db.query(Exercise).filter(Exercise.id == entry.exercise_id).first()
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    db_entry = WorkoutEntry(workout_id=workout_id, **entry.model_dump())
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)
    return db_entry


@router.get("/", response_model=list[EntryResponse])
def list_entries(workout_id: int, db: Session = Depends(get_db)):
    _get_workout_or_404(workout_id, db)
    return (
        db.query(WorkoutEntry)
        .filter(WorkoutEntry.workout_id == workout_id)
        .all()
    )


@router.put("/{entry_id}", response_model=EntryResponse)
def update_entry(
    workout_id: int,
    entry_id: int,
    updates: EntryUpdate,
    db: Session = Depends(get_db),
):
    _get_workout_or_404(workout_id, db)
    entry = (
        db.query(WorkoutEntry)
        .filter(WorkoutEntry.id == entry_id, WorkoutEntry.workout_id == workout_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{entry_id}", status_code=204)
def delete_entry(
    workout_id: int, entry_id: int, db: Session = Depends(get_db)
):
    _get_workout_or_404(workout_id, db)
    entry = (
        db.query(WorkoutEntry)
        .filter(WorkoutEntry.id == entry_id, WorkoutEntry.workout_id == workout_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
