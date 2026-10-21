from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from fitness_tracker.database import get_db
from fitness_tracker.models import Workout
from fitness_tracker.schemas import (
    WorkoutCreate,
    WorkoutDetailResponse,
    WorkoutResponse,
    WorkoutUpdate,
)

router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])


@router.post("/", response_model=WorkoutResponse, status_code=201)
def create_workout(workout: WorkoutCreate, db: Session = Depends(get_db)):
    db_workout = Workout(**workout.model_dump())
    db.add(db_workout)
    db.commit()
    db.refresh(db_workout)
    return db_workout


@router.get("/", response_model=list[WorkoutResponse])
def list_workouts(db: Session = Depends(get_db)):
    return db.query(Workout).all()


@router.get("/recent", response_model=list[WorkoutResponse])
def get_recent_workouts(limit: int = 10, db: Session = Depends(get_db)):
    """Get the most recent workouts, ordered by date (most recent first)."""
    return (
        db.query(Workout)
        .order_by(Workout.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/{workout_id}", response_model=WorkoutDetailResponse)
def get_workout(workout_id: int, db: Session = Depends(get_db)):
    workout = (
        db.query(Workout)
        .options(joinedload(Workout.entries))
        .filter(Workout.id == workout_id)
        .first()
    )
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    return workout


@router.put("/{workout_id}", response_model=WorkoutResponse)
def update_workout(
    workout_id: int, updates: WorkoutUpdate, db: Session = Depends(get_db)
):
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(workout, field, value)
    db.commit()
    db.refresh(workout)
    return workout


@router.delete("/{workout_id}", status_code=204)
def delete_workout(workout_id: int, db: Session = Depends(get_db)):
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    db.delete(workout)
    db.commit()
