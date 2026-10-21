export interface Exercise {
  id: number;
  name: string;
  muscle_group: string;
  description: string | null;
  created_at: string;
}

export interface ExerciseCreate {
  name: string;
  muscle_group: string;
  description?: string | null;
}

export interface Workout {
  id: number;
  title: string;
  workout_date: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkoutCreate {
  title: string;
  workout_date: string;
  notes?: string | null;
}

export interface WorkoutEntry {
  id: number;
  workout_id: number;
  exercise_id: number;
  sets: number;
  reps: number;
  weight: number | null;
  notes: string | null;
  created_at: string;
}

export interface EntryCreate {
  exercise_id: number;
  sets: number;
  reps: number;
  weight?: number | null;
  notes?: string | null;
}

export interface WorkoutDetail extends Workout {
  entries: WorkoutEntry[];
}
