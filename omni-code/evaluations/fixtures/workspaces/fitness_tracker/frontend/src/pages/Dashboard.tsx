import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import { Dumbbell, Library, Calendar, TrendingUp } from 'lucide-react';
import { workouts as workoutsApi, exercises as exercisesApi } from '../api';
import type { Workout, Exercise } from '../types';
import EmptyState from '../components/EmptyState';
import { SkeletonCard, SkeletonChart } from '../components/SkeletonLoader';

function timeAgo(isoString: string): string {
  const seconds = Math.floor(
    (Date.now() - new Date(isoString).getTime()) / 1000
  );
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

const MUSCLE_COLORS: Record<string, string> = {
  chest: '#f87171',
  back: '#60a5fa',
  legs: '#34d399',
  shoulders: '#fbbf24',
  arms: '#a78bfa',
  core: '#fb923c',
};

function getMuscleColor(group: string): string {
  return MUSCLE_COLORS[group.toLowerCase()] || '#7c5cfc';
}

function buildActivityData(workouts: Workout[]) {
  const counts: Record<string, number> = {};
  workouts.forEach((w) => {
    const date = w.workout_date;
    counts[date] = (counts[date] || 0) + 1;
  });
  return Object.entries(counts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, count]) => ({
      date: new Date(date + 'T00:00:00').toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
      }),
      count,
    }));
}

function buildMuscleData(exerciseList: Exercise[]) {
  const counts: Record<string, number> = {};
  exerciseList.forEach((e) => {
    const group = e.muscle_group.toLowerCase();
    counts[group] = (counts[group] || 0) + 1;
  });
  return Object.entries(counts).map(([name, value]) => ({
    name: name.charAt(0).toUpperCase() + name.slice(1),
    value,
    color: getMuscleColor(name),
  }));
}

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  show: {
    opacity: 1,
    y: 0,
    transition: { type: 'spring', damping: 20, stiffness: 200 },
  },
};

export default function Dashboard() {
  const [recent, setRecent] = useState<Workout[]>([]);
  const [allWorkouts, setAllWorkouts] = useState<Workout[]>([]);
  const [allExercises, setAllExercises] = useState<Exercise[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      workoutsApi.recent(),
      workoutsApi.list(),
      exercisesApi.list(),
    ]).then(([recentData, workoutData, exerciseData]) => {
      setRecent(recentData);
      setAllWorkouts(workoutData);
      setAllExercises(exerciseData);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div>
        <div className="h-8 w-40 bg-surface-4 rounded animate-pulse mb-8" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-10">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-10">
          <div className="lg:col-span-2">
            <SkeletonChart />
          </div>
          <SkeletonChart />
        </div>
      </div>
    );
  }

  const activityData = buildActivityData(allWorkouts);
  const muscleData = buildMuscleData(allExercises);

  const thisWeekCount = allWorkouts.filter((w) => {
    const d = new Date(w.workout_date + 'T00:00:00');
    const now = new Date();
    const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return d >= weekAgo;
  }).length;

  const stats = [
    {
      label: 'Total Workouts',
      value: allWorkouts.length,
      icon: Dumbbell,
      color: 'text-accent',
    },
    {
      label: 'Exercises',
      value: allExercises.length,
      icon: Library,
      color: 'text-success',
    },
    {
      label: 'This Week',
      value: thisWeekCount,
      icon: Calendar,
      color: 'text-warning',
    },
    {
      label: 'Recent',
      value: `${recent.length} sessions`,
      icon: TrendingUp,
      color: 'text-info',
    },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] }}
    >
      <h1 className="text-2xl font-bold text-text-primary tracking-tight mb-8">
        Dashboard
      </h1>

      {/* Stat Cards */}
      <motion.div
        className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-10"
        variants={containerVariants}
        initial="hidden"
        animate="show"
      >
        {stats.map((stat) => (
          <motion.div
            key={stat.label}
            variants={itemVariants}
            className="glass rounded-xl p-5"
          >
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-text-tertiary uppercase tracking-wider">
                {stat.label}
              </span>
              <stat.icon className={`w-4 h-4 ${stat.color}`} />
            </div>
            <div className="text-3xl font-bold text-text-primary">
              {stat.value}
            </div>
          </motion.div>
        ))}
      </motion.div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-10">
        {/* Activity Chart */}
        <motion.div
          className="lg:col-span-2 bg-surface-2 rounded-xl border border-border-subtle p-6"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <h2 className="text-sm font-medium text-text-tertiary uppercase tracking-wider mb-4">
            Workout Activity
          </h2>
          {activityData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={activityData}>
                <defs>
                  <linearGradient id="colorVolume" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#7c5cfc" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#7c5cfc" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="#1e1e2e"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tick={{ fill: '#6b6b80', fontSize: 12 }}
                  axisLine={{ stroke: '#1e1e2e' }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: '#6b6b80', fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#16161e',
                    border: '1px solid #2a2a3c',
                    borderRadius: '10px',
                    fontSize: '13px',
                  }}
                  labelStyle={{ color: '#a0a0b8' }}
                  itemStyle={{ color: '#f0f0f5' }}
                />
                <Area
                  type="monotone"
                  dataKey="count"
                  stroke="#7c5cfc"
                  strokeWidth={2}
                  fill="url(#colorVolume)"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[220px] flex items-center justify-center text-sm text-text-tertiary">
              No workout data yet
            </div>
          )}
        </motion.div>

        {/* Muscle Group Donut */}
        <motion.div
          className="bg-surface-2 rounded-xl border border-border-subtle p-6"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <h2 className="text-sm font-medium text-text-tertiary uppercase tracking-wider mb-4">
            Muscle Groups
          </h2>
          {muscleData.length > 0 ? (
            <div>
              <ResponsiveContainer width="100%" height={160}>
                <PieChart>
                  <Pie
                    data={muscleData}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={70}
                    paddingAngle={3}
                    dataKey="value"
                    stroke="none"
                  >
                    {muscleData.map((entry, index) => (
                      <Cell key={index} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#16161e',
                      border: '1px solid #2a2a3c',
                      borderRadius: '10px',
                      fontSize: '13px',
                    }}
                    labelStyle={{ color: '#a0a0b8' }}
                    itemStyle={{ color: '#f0f0f5' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex flex-wrap gap-2 mt-3 justify-center">
                {muscleData.map((entry) => (
                  <div
                    key={entry.name}
                    className="flex items-center gap-1.5 text-xs text-text-secondary"
                  >
                    <div
                      className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: entry.color }}
                    />
                    {entry.name}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="h-[160px] flex items-center justify-center text-sm text-text-tertiary">
              No exercises yet
            </div>
          )}
        </motion.div>
      </div>

      {/* Recent Workouts */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-medium text-text-tertiary uppercase tracking-wider">
          Recent Workouts
        </h2>
        <Link
          to="/workouts"
          className="text-xs text-accent hover:text-accent-hover transition-colors"
        >
          View all
        </Link>
      </div>

      {recent.length === 0 ? (
        <EmptyState
          icon={<Dumbbell className="w-7 h-7 text-text-tertiary" />}
          title="No workouts yet"
          description="Create your first workout to get started."
          action={
            <Link
              to="/workouts"
              className="px-4 py-2.5 text-sm font-medium text-white bg-accent rounded-lg hover:bg-accent-hover transition-colors"
            >
              Go to Workouts
            </Link>
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
                  Workout Date
                </th>
                <th className="text-left text-xs font-medium text-text-tertiary uppercase tracking-wider px-3 sm:px-5 py-3 hidden sm:table-cell">
                  Notes
                </th>
                <th className="text-right text-xs font-medium text-text-tertiary uppercase tracking-wider px-3 sm:px-5 py-3">
                  Logged
                </th>
              </tr>
            </thead>
            <motion.tbody
              variants={containerVariants}
              initial="hidden"
              animate="show"
            >
              {recent.map((workout) => (
                <motion.tr
                  key={workout.id}
                  variants={itemVariants}
                  className="border-b border-border-subtle last:border-b-0 hover:bg-surface-3/50 transition-colors"
                >
                  <td className="px-3 sm:px-5 py-3.5">
                    <Link
                      to={`/workouts/${workout.id}`}
                      className="font-medium text-text-primary hover:text-accent transition-colors"
                    >
                      {workout.title}
                    </Link>
                  </td>
                  <td className="px-3 sm:px-5 py-3.5 text-sm text-text-secondary">
                    {formatDate(workout.workout_date)}
                  </td>
                  <td className="px-3 sm:px-5 py-3.5 text-sm text-text-tertiary truncate max-w-xs hidden sm:table-cell">
                    {workout.notes || '—'}
                  </td>
                  <td className="px-3 sm:px-5 py-3.5 text-right text-xs text-text-tertiary whitespace-nowrap">
                    {timeAgo(workout.created_at)}
                  </td>
                </motion.tr>
              ))}
            </motion.tbody>
          </table>
        </div>
      )}
    </motion.div>
  );
}
