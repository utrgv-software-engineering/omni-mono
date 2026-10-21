from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fitness_tracker.database import get_db
from fitness_tracker.models import Exercise
from fitness_tracker.schemas import ExerciseCreate, ExerciseResponse, ExerciseUpdate

router = APIRouter(prefix="/api/v1/exercises", tags=["exercises"])


@router.post("/", response_model=ExerciseResponse, status_code=201)
def create_exercise(exercise: ExerciseCreate, db: Session = Depends(get_db)):
    db_exercise = Exercise(**exercise.model_dump())
    db.add(db_exercise)
    db.commit()
    db.refresh(db_exercise)
    return db_exercise


@router.get("/", response_model=list[ExerciseResponse])
def list_exercises(muscle_group: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Exercise)
    if muscle_group:
        query = query.filter(Exercise.muscle_group == muscle_group)
    return query.all()


@router.get("/{exercise_id}", response_model=ExerciseResponse)
def get_exercise(exercise_id: int, db: Session = Depends(get_db)):
    exercise = db.query(Exercise).filter(Exercise.id == exercise_id).first()
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return exercise


@router.put("/{exercise_id}", response_model=ExerciseResponse)
def update_exercise(
    exercise_id: int, updates: ExerciseUpdate, db: Session = Depends(get_db)
):
    exercise = db.query(Exercise).filter(Exercise.id == exercise_id).first()
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(exercise, field, value)
    db.commit()
    db.refresh(exercise)
    return exercise


@router.delete("/{exercise_id}", status_code=204)
def delete_exercise(exercise_id: int, db: Session = Depends(get_db)):
    exercise = db.query(Exercise).filter(Exercise.id == exercise_id).first()
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    db.delete(exercise)
    db.commit()
