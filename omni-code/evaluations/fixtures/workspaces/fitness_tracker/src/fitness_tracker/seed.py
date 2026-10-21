"""Seed the database with sample data.

Run with: uv run python -m fitness_tracker.seed
"""

from datetime import date, timedelta

from fitness_tracker.database import Base, SessionLocal, engine
from fitness_tracker.models import Exercise, Workout, WorkoutEntry


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Clear existing data
    db.query(WorkoutEntry).delete()
    db.query(Workout).delete()
    db.query(Exercise).delete()
    db.commit()

    # --- Exercises ---
    bench = Exercise(name="Bench Press", muscle_group="chest", description="Flat barbell bench press")
    squat = Exercise(name="Barbell Squat", muscle_group="legs", description="Back squat with barbell")
    deadlift = Exercise(name="Deadlift", muscle_group="back", description="Conventional deadlift")
    curl = Exercise(name="Bicep Curl", muscle_group="arms", description="Dumbbell bicep curl")
    ohp = Exercise(name="Overhead Press", muscle_group="shoulders", description="Standing barbell overhead press")

    db.add_all([bench, squat, deadlift, curl, ohp])
    db.commit()

    today = date.today()

    # --- Workouts ---
    # Workout A: 3 days ago, created 3 days ago (normal)
    workout_a = Workout(
        title="Upper Body Day",
        workout_date=today - timedelta(days=3),
        notes="Felt strong today",
    )

    # Workout B: 2 days ago, created 2 days ago (normal)
    workout_b = Workout(
        title="Leg Day",
        workout_date=today - timedelta(days=2),
        notes="Heavy squats",
    )

    # Workout C: yesterday, created yesterday (normal)
    workout_c = Workout(
        title="Pull Day",
        workout_date=today - timedelta(days=1),
        notes="Back and biceps",
    )

    # Workout D: happened 5 days ago but logged today (backdated)
    workout_d = Workout(
        title="Makeup Cardio Session",
        workout_date=today - timedelta(days=5),
        notes="Forgot to log this last week",
    )

    db.add_all([workout_a, workout_b, workout_c, workout_d])
    db.commit()

    # --- Entries ---
    entries = [
        WorkoutEntry(workout_id=workout_a.id, exercise_id=bench.id, sets=4, reps=8, weight=185.0),
        WorkoutEntry(workout_id=workout_a.id, exercise_id=ohp.id, sets=3, reps=10, weight=95.0),
        WorkoutEntry(workout_id=workout_b.id, exercise_id=squat.id, sets=5, reps=5, weight=275.0),
        WorkoutEntry(workout_id=workout_c.id, exercise_id=deadlift.id, sets=3, reps=5, weight=315.0),
        WorkoutEntry(workout_id=workout_c.id, exercise_id=curl.id, sets=3, reps=12, weight=30.0),
        WorkoutEntry(workout_id=workout_d.id, exercise_id=bench.id, sets=3, reps=10, weight=135.0, notes="Light session"),
    ]

    db.add_all(entries)
    db.commit()
    db.close()

    print("Database seeded successfully!")
    print(f"  Exercises: 5")
    print(f"  Workouts: 4")
    print(f"  Entries: 6")


if __name__ == "__main__":
    seed()
