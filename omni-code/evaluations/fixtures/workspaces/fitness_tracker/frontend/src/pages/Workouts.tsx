import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Plus, Pencil, Trash2, Dumbbell } from 'lucide-react';
import { workouts as workoutsApi } from '../api';
import type { Workout, WorkoutCreate } from '../types';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import PageHeader from '../components/PageHeader';
import { SkeletonTable } from '../components/SkeletonLoader';

const INPUT =
  'w-full bg-surface-4 border border-border-default rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder:text-text-disabled focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent transition-colors';
const LABEL = 'block text-sm font-medium text-text-secondary mb-1.5';

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.03 } },
};

const rowVariants = {
  hidden: { opacity: 0 },
  show: { opacity: 1 },
};

export default function Workouts() {
  const [items, setItems] = useState<Workout[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Workout | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Workout | null>(null);
  const [form, setForm] = useState<WorkoutCreate>({
    title: '',
    workout_date: new Date().toISOString().slice(0, 10),
    notes: '',
  });

  const load = () => {
    workoutsApi.list().then((data) => {
      setItems(data);
      setLoading(false);
    });
  };

  useEffect(load, []);

  const openCreate = () => {
    setEditing(null);
    setForm({
      title: '',
      workout_date: new Date().toISOString().slice(0, 10),
      notes: '',
    });
    setModalOpen(true);
  };

  const openEdit = (w: Workout) => {
    setEditing(w);
    setForm({
      title: w.title,
      workout_date: w.workout_date,
      notes: w.notes || '',
    });
    setModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (editing) {
      await workoutsApi.update(editing.id, form);
    } else {
      await workoutsApi.create(form);
    }
    setModalOpen(false);
    load();
  };

  const handleDelete = async () => {
    if (deleteTarget) {
      await workoutsApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      load();
    }
  };

  if (loading) {
    return (
      <div>
        <div className="h-8 w-32 bg-surface-4 rounded animate-pulse mb-8" />
        <SkeletonTable rows={6} />
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
        title="Workouts"
        description={`${items.length} workout${items.length !== 1 ? 's' : ''} logged`}
        action={
          <button
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
          >
            <Plus className="w-4 h-4" />
            New Workout
          </button>
        }
      />

      {items.length === 0 ? (
        <EmptyState
          icon={<Dumbbell className="w-7 h-7 text-text-tertiary" />}
          title="No workouts yet"
          description="Start tracking by creating your first workout."
          action={
            <button
              onClick={openCreate}
              className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover active:scale-[0.98] transition-all cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              New Workout
            </button>
          }
        />
      ) : (
        <div className="bg-surface-2 rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle">
                <th className="text-left text-xs font-medium text-text-tertiary uppercase tracking-wider px-3 sm:px-5 py-3">
                  Title
                </th>
                <th className="text-left text-xs font-medium text-text-tertiary uppercase tracking-wider px-3 sm:px-5 py-3">
                  Date
                </th>
                <th className="text-left text-xs font-medium text-text-tertiary uppercase tracking-wider px-3 sm:px-5 py-3 hidden sm:table-cell">
                  Notes
                </th>
                <th className="text-right text-xs font-medium text-text-tertiary uppercase tracking-wider px-3 sm:px-5 py-3">
                  Actions
                </th>
              </tr>
            </thead>
            <motion.tbody
              variants={containerVariants}
              initial="hidden"
              animate="show"
            >
              {items.map((w) => (
                <motion.tr
                  key={w.id}
                  variants={rowVariants}
                  className="border-b border-border-subtle last:border-b-0 hover:bg-surface-3/50 transition-colors"
                >
                  <td className="px-3 sm:px-5 py-3.5">
                    <Link
                      to={`/workouts/${w.id}`}
                      className="font-medium text-text-primary hover:text-accent transition-colors"
                    >
                      {w.title}
                    </Link>
                  </td>
                  <td className="px-3 sm:px-5 py-3.5 text-sm text-text-secondary">
                    {formatDate(w.workout_date)}
                  </td>
                  <td className="px-3 sm:px-5 py-3.5 text-sm text-text-tertiary truncate max-w-xs hidden sm:table-cell">
                    {w.notes || '—'}
                  </td>
                  <td className="px-3 sm:px-5 py-3.5 text-right">
                    <button
                      onClick={() => openEdit(w)}
                      className="p-1.5 rounded-lg text-text-tertiary hover:text-accent hover:bg-surface-3 transition-colors cursor-pointer mr-1"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => setDeleteTarget(w)}
                      className="p-1.5 rounded-lg text-text-tertiary hover:text-danger hover:bg-danger-subtle transition-colors cursor-pointer"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </motion.tr>
              ))}
            </motion.tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editing ? 'Edit Workout' : 'New Workout'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className={LABEL}>Title</label>
            <input
              type="text"
              required
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              className={INPUT}
              placeholder="e.g. Upper Body Day"
            />
          </div>
          <div>
            <label className={LABEL}>Workout Date</label>
            <input
              type="date"
              required
              value={form.workout_date}
              onChange={(e) =>
                setForm({ ...form, workout_date: e.target.value })
              }
              className={INPUT}
            />
          </div>
          <div>
            <label className={LABEL}>Notes</label>
            <textarea
              value={form.notes || ''}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              rows={3}
              className={INPUT}
              placeholder="How did it go?"
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
              {editing ? 'Save Changes' : 'Create Workout'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirm */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete Workout"
        message={`Are you sure you want to delete "${deleteTarget?.title}"? This will also delete all its entries.`}
      />
    </motion.div>
  );
}
