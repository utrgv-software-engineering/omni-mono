import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Plus, Pencil, Trash2, Library } from 'lucide-react';
import { exercises as exercisesApi } from '../api';
import type { Exercise, ExerciseCreate } from '../types';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import PageHeader from '../components/PageHeader';
import { SkeletonCard } from '../components/SkeletonLoader';

const INPUT =
  'w-full bg-surface-4 border border-border-default rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder:text-text-disabled focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent transition-colors';
const LABEL = 'block text-sm font-medium text-text-secondary mb-1.5';

const MUSCLE_COLORS: Record<string, string> = {
  chest: 'bg-[#f87171]/15 text-[#f87171]',
  back: 'bg-[#60a5fa]/15 text-[#60a5fa]',
  legs: 'bg-[#34d399]/15 text-[#34d399]',
  shoulders: 'bg-[#fbbf24]/15 text-[#fbbf24]',
  arms: 'bg-[#a78bfa]/15 text-[#a78bfa]',
  core: 'bg-[#fb923c]/15 text-[#fb923c]',
};

function getMuscleStyle(group: string): string {
  return MUSCLE_COLORS[group.toLowerCase()] || 'bg-accent-subtle text-accent';
}

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.04 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 10 },
  show: {
    opacity: 1,
    y: 0,
    transition: { type: 'spring', damping: 20, stiffness: 200 },
  },
};

export default function Exercises() {
  const [items, setItems] = useState<Exercise[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [muscleGroups, setMuscleGroups] = useState<string[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Exercise | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Exercise | null>(null);
  const [form, setForm] = useState<ExerciseCreate>({
    name: '',
    muscle_group: '',
    description: '',
  });

  const load = async (mg?: string) => {
    const data = await exercisesApi.list(mg || undefined);
    setItems(data);
    setLoading(false);
  };

  useEffect(() => {
    exercisesApi.list().then((all) => {
      const groups = [...new Set(all.map((e) => e.muscle_group))].sort();
      setMuscleGroups(groups);
      setItems(all);
      setLoading(false);
    });
  }, []);

  const handleFilter = (mg: string) => {
    setFilter(mg);
    load(mg);
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', muscle_group: '', description: '' });
    setModalOpen(true);
  };

  const openEdit = (ex: Exercise) => {
    setEditing(ex);
    setForm({
      name: ex.name,
      muscle_group: ex.muscle_group,
      description: ex.description || '',
    });
    setModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (editing) {
      await exercisesApi.update(editing.id, form);
    } else {
      await exercisesApi.create(form);
    }
    setModalOpen(false);
    const all = await exercisesApi.list();
    setMuscleGroups([...new Set(all.map((e) => e.muscle_group))].sort());
    load(filter);
  };

  const handleDelete = async () => {
    if (deleteTarget) {
      await exercisesApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      const all = await exercisesApi.list();
      setMuscleGroups([...new Set(all.map((e) => e.muscle_group))].sort());
      load(filter);
    }
  };

  if (loading) {
    return (
      <div>
        <div className="h-8 w-44 bg-surface-4 rounded animate-pulse mb-8" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
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
      <PageHeader
        title="Exercise Library"
        description={`${items.length} exercise${items.length !== 1 ? 's' : ''} in your library`}
        action={
          <button
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
          >
            <Plus className="w-4 h-4" />
            New Exercise
          </button>
        }
      />

      {/* Filter Pills */}
      {muscleGroups.length > 0 && (
        <div className="flex gap-2 mb-6 flex-wrap">
          <button
            onClick={() => handleFilter('')}
            className={`px-3.5 py-1.5 text-sm rounded-full transition-all duration-200 cursor-pointer ${
              filter === ''
                ? 'bg-accent text-white shadow-[0_0_12px_rgba(124,92,252,0.4)]'
                : 'bg-surface-3 text-text-secondary hover:text-text-primary hover:bg-surface-4 border border-border-subtle'
            }`}
          >
            All
          </button>
          {muscleGroups.map((mg) => (
            <button
              key={mg}
              onClick={() => handleFilter(mg)}
              className={`px-3.5 py-1.5 text-sm rounded-full capitalize transition-all duration-200 cursor-pointer ${
                filter === mg
                  ? 'bg-accent text-white shadow-[0_0_12px_rgba(124,92,252,0.4)]'
                  : 'bg-surface-3 text-text-secondary hover:text-text-primary hover:bg-surface-4 border border-border-subtle'
              }`}
            >
              {mg}
            </button>
          ))}
        </div>
      )}

      {items.length === 0 ? (
        <EmptyState
          icon={<Library className="w-7 h-7 text-text-tertiary" />}
          title={filter ? 'No exercises in this group' : 'No exercises yet'}
          description={
            filter
              ? 'Try a different muscle group or create a new exercise.'
              : 'Add exercises to build your library.'
          }
          action={
            <button
              onClick={openCreate}
              className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              New Exercise
            </button>
          }
        />
      ) : (
        <motion.div
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
          variants={containerVariants}
          initial="hidden"
          animate="show"
        >
          {items.map((ex) => (
            <motion.div
              key={ex.id}
              variants={itemVariants}
              className="bg-surface-2 rounded-xl border border-border-subtle p-5 card-hover group"
            >
              <div className="flex items-start justify-between mb-3">
                <h3 className="font-semibold text-text-primary group-hover:text-accent transition-colors">
                  {ex.name}
                </h3>
                <span
                  className={`text-xs px-2.5 py-0.5 rounded-full capitalize font-medium ${getMuscleStyle(ex.muscle_group)}`}
                >
                  {ex.muscle_group}
                </span>
              </div>
              {ex.description && (
                <p className="text-sm text-text-tertiary mb-4 line-clamp-2">
                  {ex.description}
                </p>
              )}
              <div className="flex gap-1 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                <button
                  onClick={() => openEdit(ex)}
                  className="p-1.5 rounded-md text-text-tertiary hover:text-accent hover:bg-surface-3 transition-colors cursor-pointer"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setDeleteTarget(ex)}
                  className="p-1.5 rounded-md text-text-tertiary hover:text-danger hover:bg-danger-subtle transition-colors cursor-pointer"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </motion.div>
          ))}
        </motion.div>
      )}

      {/* Create/Edit Modal */}
      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editing ? 'Edit Exercise' : 'New Exercise'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className={LABEL}>Name</label>
            <input
              type="text"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className={INPUT}
              placeholder="e.g. Bench Press"
            />
          </div>
          <div>
            <label className={LABEL}>Muscle Group</label>
            <input
              type="text"
              required
              value={form.muscle_group}
              onChange={(e) =>
                setForm({ ...form, muscle_group: e.target.value })
              }
              className={INPUT}
              placeholder="e.g. chest, legs, back"
            />
          </div>
          <div>
            <label className={LABEL}>Description</label>
            <textarea
              value={form.description || ''}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
              rows={3}
              className={INPUT}
              placeholder="Optional description"
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setModalOpen(false)}
              className="px-4 py-2.5 text-sm font-medium text-text-secondary bg-surface-4 border border-border-default rounded-lg hover:bg-surface-3 hover:border-border-hover transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
            >
              {editing ? 'Save Changes' : 'Create Exercise'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirm */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete Exercise"
        message={`Are you sure you want to delete "${deleteTarget?.name}"?`}
      />
    </motion.div>
  );
}
