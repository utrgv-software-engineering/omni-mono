import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ArrowLeft, Plus, Pencil, Trash2, Dumbbell, ClipboardList, Calendar } from 'lucide-react';
import {
  workouts as workoutsApi,
  entries as entriesApi,
  exercises as exercisesApi,
} from '../api';
import type {
  WorkoutDetail as WorkoutDetailType,
  WorkoutEntry,
  Exercise,
  EntryCreate,
} from '../types';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import { SkeletonCard } from '../components/SkeletonLoader';

const INPUT =
  'w-full bg-surface-4 border border-border-default rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder:text-text-disabled focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent transition-colors';
const LABEL = 'block text-sm font-medium text-text-secondary mb-1.5';

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.04 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 8 },
  show: {
    opacity: 1,
    y: 0,
    transition: { type: 'spring', damping: 20, stiffness: 200 },
  },
};

export default function WorkoutDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const workoutId = Number(id);

  const [workout, setWorkout] = useState<WorkoutDetailType | null>(null);
  const [exerciseList, setExerciseList] = useState<Exercise[]>([]);
  const [loading, setLoading] = useState(true);
  const [entryModalOpen, setEntryModalOpen] = useState(false);
  const [editingEntry, setEditingEntry] = useState<WorkoutEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkoutEntry | null>(null);
  const [deleteWorkout, setDeleteWorkout] = useState(false);
  const [entryForm, setEntryForm] = useState<EntryCreate>({
    exercise_id: 0,
    sets: 3,
    reps: 10,
    weight: undefined,
    notes: '',
  });

  const load = async () => {
    const [w, exList] = await Promise.all([
      workoutsApi.get(workoutId),
      exercisesApi.list(),
    ]);
    setWorkout(w);
    setExerciseList(exList);
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, [workoutId]);

  const exerciseName = (exerciseId: number) =>
    exerciseList.find((e) => e.id === exerciseId)?.name || `Exercise #${exerciseId}`;

  const openAddEntry = () => {
    setEditingEntry(null);
    setEntryForm({
      exercise_id: exerciseList[0]?.id || 0,
      sets: 3,
      reps: 10,
      weight: undefined,
      notes: '',
    });
    setEntryModalOpen(true);
  };

  const openEditEntry = (entry: WorkoutEntry) => {
    setEditingEntry(entry);
    setEntryForm({
      exercise_id: entry.exercise_id,
      sets: entry.sets,
      reps: entry.reps,
      weight: entry.weight ?? undefined,
      notes: entry.notes || '',
    });
    setEntryModalOpen(true);
  };

  const handleEntrySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (editingEntry) {
      await entriesApi.update(workoutId, editingEntry.id, {
        sets: entryForm.sets,
        reps: entryForm.reps,
        weight: entryForm.weight,
        notes: entryForm.notes,
      });
    } else {
      await entriesApi.create(workoutId, entryForm);
    }
    setEntryModalOpen(false);
    load();
  };

  const handleDeleteEntry = async () => {
    if (deleteTarget) {
      await entriesApi.delete(workoutId, deleteTarget.id);
      setDeleteTarget(null);
      load();
    }
  };

  const handleDeleteWorkout = async () => {
    await workoutsApi.delete(workoutId);
    navigate('/workouts');
  };

  if (loading || !workout) {
    return (
      <div>
        <div className="h-4 w-32 bg-surface-4 rounded animate-pulse mb-6" />
        <SkeletonCard />
        <div className="mt-6 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] }}
    >
      {/* Back link */}
      <Link
        to="/workouts"
        className="inline-flex items-center gap-1.5 text-sm text-text-tertiary hover:text-text-primary transition-colors mb-6"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to Workouts
      </Link>

      {/* Workout header */}
      <div className="glass rounded-xl p-5 sm:p-6 mb-8">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-bold text-text-primary tracking-tight mb-2">
              {workout.title}
            </h1>
            <div className="flex items-center gap-2 text-sm text-accent font-medium mb-1">
              <Calendar className="w-4 h-4 shrink-0" />
              {new Date(workout.workout_date + 'T00:00:00').toLocaleDateString(
                'en-US',
                {
                  weekday: 'long',
                  month: 'long',
                  day: 'numeric',
                  year: 'numeric',
                }
              )}
            </div>
            {workout.notes && (
              <p className="text-sm text-text-secondary mt-3">{workout.notes}</p>
            )}
          </div>
          <button
            onClick={() => setDeleteWorkout(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-danger bg-danger-subtle rounded-lg hover:bg-danger/20 transition-colors cursor-pointer self-start shrink-0"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Delete
          </button>
        </div>
      </div>

      {/* Entries header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-medium text-text-tertiary uppercase tracking-wider">
          Exercises ({workout.entries.length})
        </h2>
        <button
          onClick={openAddEntry}
          className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
        >
          <Plus className="w-4 h-4" />
          Add Exercise
        </button>
      </div>

      {workout.entries.length === 0 ? (
        <EmptyState
          icon={<ClipboardList className="w-7 h-7 text-text-tertiary" />}
          title="No exercises logged"
          description="Add exercises to this workout."
          action={
            <button
              onClick={openAddEntry}
              className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              Add Exercise
            </button>
          }
        />
      ) : (
        <motion.div
          className="space-y-3"
          variants={containerVariants}
          initial="hidden"
          animate="show"
        >
          {workout.entries.map((entry) => (
            <motion.div
              key={entry.id}
              variants={itemVariants}
              className="bg-surface-2 rounded-xl border border-border-subtle p-4 flex items-center justify-between card-hover"
            >
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-accent-subtle rounded-lg flex items-center justify-center">
                  <Dumbbell className="w-5 h-5 text-accent" />
                </div>
                <div>
                  <div className="font-medium text-text-primary">
                    {exerciseName(entry.exercise_id)}
                  </div>
                  <div className="text-sm text-text-secondary">
                    {entry.sets} sets &times; {entry.reps} reps
                    {entry.weight != null && (
                      <span className="ml-1 text-text-tertiary">
                        @ {entry.weight} lbs
                      </span>
                    )}
                    {entry.notes && (
                      <span className="ml-2 text-text-tertiary">
                        &mdash; {entry.notes}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex gap-1">
                <button
                  onClick={() => openEditEntry(entry)}
                  className="p-2 rounded-lg text-text-tertiary hover:text-accent hover:bg-surface-3 transition-colors cursor-pointer"
                >
                  <Pencil className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setDeleteTarget(entry)}
                  className="p-2 rounded-lg text-text-tertiary hover:text-danger hover:bg-danger-subtle transition-colors cursor-pointer"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </motion.div>
          ))}
        </motion.div>
      )}

      {/* Entry Create/Edit Modal */}
      <Modal
        open={entryModalOpen}
        onClose={() => setEntryModalOpen(false)}
        title={editingEntry ? 'Edit Exercise Entry' : 'Add Exercise'}
      >
        <form onSubmit={handleEntrySubmit} className="space-y-4">
          {!editingEntry && (
            <div>
              <label className={LABEL}>Exercise</label>
              <select
                required
                value={entryForm.exercise_id}
                onChange={(e) =>
                  setEntryForm({
                    ...entryForm,
                    exercise_id: Number(e.target.value),
                  })
                }
                className={INPUT}
              >
                {exerciseList.map((ex) => (
                  <option key={ex.id} value={ex.id}>
                    {ex.name} ({ex.muscle_group})
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={LABEL}>Sets</label>
              <input
                type="number"
                required
                min={1}
                value={entryForm.sets}
                onChange={(e) =>
                  setEntryForm({ ...entryForm, sets: Number(e.target.value) })
                }
                className={INPUT}
              />
            </div>
            <div>
              <label className={LABEL}>Reps</label>
              <input
                type="number"
                required
                min={1}
                value={entryForm.reps}
                onChange={(e) =>
                  setEntryForm({ ...entryForm, reps: Number(e.target.value) })
                }
                className={INPUT}
              />
            </div>
            <div>
              <label className={LABEL}>Weight</label>
              <input
                type="number"
                step="0.5"
                min={0}
                value={entryForm.weight ?? ''}
                onChange={(e) =>
                  setEntryForm({
                    ...entryForm,
                    weight: e.target.value ? Number(e.target.value) : undefined,
                  })
                }
                placeholder="lbs"
                className={INPUT}
              />
            </div>
          </div>
          <div>
            <label className={LABEL}>Notes</label>
            <input
              type="text"
              value={entryForm.notes || ''}
              onChange={(e) =>
                setEntryForm({ ...entryForm, notes: e.target.value })
              }
              className={INPUT}
              placeholder="Optional notes"
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setEntryModalOpen(false)}
              className="px-4 py-2.5 text-sm font-medium text-text-secondary bg-surface-4 border border-border-default rounded-lg hover:bg-surface-3 hover:border-border-hover transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
            >
              {editingEntry ? 'Save Changes' : 'Add Exercise'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Delete Entry Confirm */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteEntry}
        title="Delete Entry"
        message={`Remove ${deleteTarget ? exerciseName(deleteTarget.exercise_id) : ''} from this workout?`}
      />

      {/* Delete Workout Confirm */}
      <ConfirmDialog
        open={deleteWorkout}
        onClose={() => setDeleteWorkout(false)}
        onConfirm={handleDeleteWorkout}
        title="Delete Workout"
        message={`Are you sure you want to delete "${workout.title}" and all its entries?`}
      />
    </motion.div>
  );
}
