import type {
  Exercise,
  ExerciseCreate,
  Workout,
  WorkoutCreate,
  WorkoutDetail,
  WorkoutEntry,
  EntryCreate,
} from './types';

const BASE = '/api/v1';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// Exercises
export const exercises = {
  list: (muscleGroup?: string) =>
    request<Exercise[]>(
      `/exercises/${muscleGroup ? `?muscle_group=${encodeURIComponent(muscleGroup)}` : ''}`
    ),
  get: (id: number) => request<Exercise>(`/exercises/${id}`),
  create: (data: ExerciseCreate) =>
    request<Exercise>('/exercises/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: number, data: Partial<ExerciseCreate>) =>
    request<Exercise>(`/exercises/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: number) =>
    request<void>(`/exercises/${id}`, { method: 'DELETE' }),
};

// Workouts
export const workouts = {
  list: () => request<Workout[]>('/workouts/'),
  recent: (limit = 10) => request<Workout[]>(`/workouts/recent?limit=${limit}`),
  get: (id: number) => request<WorkoutDetail>(`/workouts/${id}`),
  create: (data: WorkoutCreate) =>
    request<Workout>('/workouts/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: number, data: Partial<WorkoutCreate>) =>
    request<Workout>(`/workouts/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: number) =>
    request<void>(`/workouts/${id}`, { method: 'DELETE' }),
};

// Entries
export const entries = {
  list: (workoutId: number) =>
    request<WorkoutEntry[]>(`/workouts/${workoutId}/entries/`),
  create: (workoutId: number, data: EntryCreate) =>
    request<WorkoutEntry>(`/workouts/${workoutId}/entries/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (workoutId: number, entryId: number, data: Partial<EntryCreate>) =>
    request<WorkoutEntry>(`/workouts/${workoutId}/entries/${entryId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  delete: (workoutId: number, entryId: number) =>
    request<void>(`/workouts/${workoutId}/entries/${entryId}`, { method: 'DELETE' }),
};
