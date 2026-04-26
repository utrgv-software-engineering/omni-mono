export function SkeletonCard() {
  return (
    <div className="bg-surface-2 rounded-xl border border-border-subtle p-5 animate-pulse">
      <div className="h-4 w-2/3 bg-surface-4 rounded mb-3" />
      <div className="h-3 w-1/3 bg-surface-4 rounded mb-2" />
      <div className="h-3 w-full bg-surface-4 rounded" />
    </div>
  );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div className="bg-surface-2 rounded-xl border border-border-subtle overflow-hidden">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex gap-4 px-5 py-4 border-b border-border-subtle last:border-b-0 animate-pulse"
        >
          <div className="h-4 w-1/4 bg-surface-4 rounded" />
          <div className="h-4 w-1/6 bg-surface-4 rounded" />
          <div className="h-4 w-1/3 bg-surface-4 rounded" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonChart() {
  return (
    <div className="bg-surface-2 rounded-xl border border-border-subtle p-6 animate-pulse">
      <div className="h-4 w-1/4 bg-surface-4 rounded mb-6" />
      <div className="h-[200px] bg-surface-4/50 rounded" />
    </div>
  );
}
