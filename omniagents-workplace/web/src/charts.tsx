// Plain-SVG charts for the Factory page: lines over time (with an interval
// band and promotion markers) and a cost-vs-correctness frontier. Thin marks,
// recessive grid, one y-axis per chart, hover tooltips; series colors are
// the --series-N slots in index.css, text stays in text colors.
import { type PointerEvent, useEffect, useMemo, useRef, useState } from "react";

export interface Series {
  name: string;
  points: { x: number; y: number | null; low?: number; high?: number; note?: string }[];
}

export interface Marker {
  x: number;
  label: string;
  title: string;
  href?: string;
}

const H = 200;

/** A chart's drawing width: its figure's, so text keeps its size on a phone
 * instead of shrinking with a scaled-down viewBox. */
function useChartWidth() {
  const ref = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(560);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(240, Math.round(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}
const PAD = { left: 48, right: 16, top: 12, bottom: 28 };

const colour = (i: number) => `var(--series-${(i % 3) + 1})`;

function ticks(min: number, max: number, count = 4): number[] {
  if (max <= min) return [min];
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

export function TimeChart({
  title,
  series,
  markers = [],
  format,
  yMax,
}: {
  title: string;
  series: Series[];
  markers?: Marker[];
  format: (v: number) => string;
  yMax?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [ref, W] = useChartWidth();
  const xs = useMemo(
    () =>
      [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort(
        (a, b) => a - b,
      ),
    [series],
  );
  const values = series.flatMap((s) =>
    s.points.flatMap((p) => [p.y, p.high].filter((v): v is number => v != null)),
  );
  if (xs.length === 0 || values.length === 0)
    return (
      <figure ref={ref} className="rounded-md border p-3">
        <figcaption className="text-sm font-semibold">{title}</figcaption>
        <p className="py-6 text-center text-xs text-muted-foreground">
          Nothing to plot in this period.
        </p>
      </figure>
    );
  const x0 = Math.min(xs[0], ...markers.map((m) => m.x));
  const x1 = Math.max(xs[xs.length - 1], ...markers.map((m) => m.x));
  const top = yMax ?? (Math.max(...values) * 1.1 || 1);
  const sx = (x: number) =>
    PAD.left + (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0)) * (W - PAD.left - PAD.right);
  const sy = (y: number) => PAD.top + (1 - y / top) * (H - PAD.top - PAD.bottom);
  const day = (x: number) =>
    new Date(x * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const at = x0 + ((e.clientX - box.left) / box.width) * (x1 - x0);
    let best = xs[0];
    for (const x of xs) if (Math.abs(x - at) < Math.abs(best - at)) best = x;
    setHover(best);
  };
  // Promotion labels at least 28px apart (a marker's line and title stay).
  const labelled = new Set<number>();
  let lastLabel = -Infinity;
  [...markers.keys()]
    .sort((a, b) => markers[a]!.x - markers[b]!.x)
    .forEach((i) => {
      const at = sx(markers[i]!.x);
      if (at - lastLabel >= 28) {
        labelled.add(i);
        lastLabel = at;
      }
    });
  // About one date label per 90px.
  const every = Math.max(1, Math.ceil(xs.length / Math.max(2, Math.floor((W - PAD.left) / 90))));
  return (
    <figure ref={ref} className="viz rounded-md border p-3">
      <figcaption className="flex flex-wrap items-center gap-3 text-sm font-semibold">
        {title}
        {series.length > 1 &&
          series.map((s, i) => (
            <span key={s.name} className="flex items-center gap-1 text-xs font-normal">
              <svg width="14" height="4" aria-hidden>
                <line x1="0" y1="2" x2="14" y2="2" stroke={colour(i)} strokeWidth="2" />
              </svg>
              {s.name}
            </span>
          ))}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="mt-1 max-w-full" role="img" aria-label={title}>
        {ticks(0, top).map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={sy(t)}
              y2={sy(t)}
              className="stroke-border"
              strokeWidth="1"
            />
            <text x={PAD.left - 6} y={sy(t) + 3} textAnchor="end" className="fill-muted-foreground text-[10px]">
              {format(t)}
            </text>
          </g>
        ))}
        {xs.map((x, i) =>
          i % every === 0 ? (
            <text key={x} x={sx(x)} y={H - 8} textAnchor="middle" className="fill-muted-foreground text-[10px]">
              {day(x)}
            </text>
          ) : null,
        )}
        {markers.map((m, i) => (
          <g key={`${m.x}${m.label}`}>
            <title>{m.title}</title>
            <line
              x1={sx(m.x)}
              x2={sx(m.x)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              className="stroke-muted-foreground"
              strokeWidth="1"
              strokeDasharray="2 3"
            />
            {/* A label only where it has room; each line keeps its title. */}
            {labelled.has(i) && (
              <text x={sx(m.x) + 3} y={PAD.top + 9} className="fill-muted-foreground text-[10px]">
                {m.label}
              </text>
            )}
          </g>
        ))}
        {series.map((s, i) => {
          const pts = s.points.filter((p) => p.y != null);
          const band = pts.filter((p) => p.low != null && p.high != null);
          return (
            <g key={s.name}>
              {band.length > 1 && (
                <path
                  d={`M${band.map((p) => `${sx(p.x)},${sy(p.high!)}`).join("L")}L${[...band]
                    .reverse()
                    .map((p) => `${sx(p.x)},${sy(p.low!)}`)
                    .join("L")}Z`}
                  fill={colour(i)}
                  opacity="0.15"
                />
              )}
              {pts.length > 1 && (
                <path
                  d={`M${pts.map((p) => `${sx(p.x)},${sy(p.y!)}`).join("L")}`}
                  fill="none"
                  stroke={colour(i)}
                  strokeWidth="2"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )}
              {pts.map((p) => (
                <circle
                  key={p.x}
                  cx={sx(p.x)}
                  cy={sy(p.y!)}
                  r={hover === p.x ? 5 : 4}
                  fill={colour(i)}
                  className="stroke-background"
                  strokeWidth="2"
                />
              ))}
            </g>
          );
        })}
        {hover != null && (
          <line
            x1={sx(hover)}
            x2={sx(hover)}
            y1={PAD.top}
            y2={H - PAD.bottom}
            className="stroke-muted-foreground"
            strokeWidth="1"
          />
        )}
        <rect
          x={PAD.left}
          y={PAD.top}
          width={W - PAD.left - PAD.right}
          height={H - PAD.top - PAD.bottom}
          fill="transparent"
          onPointerMove={onMove}
          onPointerDown={onMove}
          onPointerLeave={(e) => e.pointerType === "mouse" && setHover(null)}
        />
      </svg>
      {hover != null && (
        <div className="mt-1 rounded border bg-popover px-2 py-1 text-xs shadow-sm">
          <div className="font-medium">{day(hover)}</div>
          {series.map((s, i) => {
            const p = s.points.find((q) => q.x === hover);
            return (
              <div key={s.name} className="flex items-center gap-1">
                <svg width="8" height="8" aria-hidden>
                  <circle cx="4" cy="4" r="4" fill={colour(i)} />
                </svg>
                {s.name}: {p?.y == null ? "–" : format(p.y)}
                {p?.low != null && p.high != null && (
                  <span className="text-muted-foreground">
                    {" "}
                    ({format(p.low)}–{format(p.high)})
                  </span>
                )}
                {p?.note && <span className="text-muted-foreground"> · {p.note}</span>}
              </div>
            );
          })}
        </div>
      )}
    </figure>
  );
}

export interface FrontierPoint {
  name: string;
  cost: number;
  correctness: number;
  runs: number;
}

/** The configurations no other beats on both cost and correctness. */
export function frontier(points: FrontierPoint[]): Set<string> {
  return new Set(
    points
      .filter(
        (p) =>
          !points.some(
            (q) =>
              q !== p &&
              q.cost <= p.cost &&
              q.correctness >= p.correctness &&
              (q.cost < p.cost || q.correctness > p.correctness),
          ),
      )
      .map((p) => p.name),
  );
}

export function FrontierChart({
  title,
  points,
  bar,
}: {
  title: string;
  points: FrontierPoint[];
  bar: number;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const [ref, W] = useChartWidth();
  const best = frontier(points);
  const maxCost = Math.max(...points.map((p) => p.cost), 0.01) * 1.15;
  const sx = (c: number) => PAD.left + (c / maxCost) * (W - PAD.left - PAD.right);
  const sy = (r: number) => PAD.top + (1 - r) * (H - PAD.top - PAD.bottom);
  const line = [...points].filter((p) => best.has(p.name)).sort((a, b) => a.cost - b.cost);
  return (
    <figure ref={ref} className="viz rounded-md border p-3">
      <figcaption className="text-sm font-semibold">
        {title}{" "}
        <span className="text-xs font-normal text-muted-foreground">
          up and to the left is better
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="mt-1 max-w-full" role="img" aria-label={title}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} className="stroke-border" strokeWidth="1" />
            <text x={PAD.left - 6} y={sy(t) + 3} textAnchor="end" className="fill-muted-foreground text-[10px]">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {ticks(0, maxCost).map((t) => (
          <text key={t} x={sx(t)} y={H - 8} textAnchor="middle" className="fill-muted-foreground text-[10px]">
            ${t.toFixed(2)}
          </text>
        ))}
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={sy(bar)}
          y2={sy(bar)}
          className="stroke-muted-foreground"
          strokeWidth="1"
          strokeDasharray="2 3"
        />
        {line.length > 1 && (
          <path
            d={`M${line.map((p) => `${sx(p.cost)},${sy(p.correctness)}`).join("L")}`}
            fill="none"
            stroke="var(--series-1)"
            strokeWidth="2"
          />
        )}
        {points.map((p) => (
          <g
            key={p.name}
            onPointerEnter={() => setHover(p.name)}
            onPointerDown={() => setHover(p.name)}
            onPointerLeave={(e) => e.pointerType === "mouse" && setHover(null)}
          >
            <circle cx={sx(p.cost)} cy={sy(p.correctness)} r="12" fill="transparent" />
            <circle
              cx={sx(p.cost)}
              cy={sy(p.correctness)}
              r={hover === p.name ? 6 : 5}
              fill={best.has(p.name) ? "var(--series-1)" : "var(--series-2)"}
              className="stroke-background"
              strokeWidth="2"
            />
            <text x={sx(p.cost) + 8} y={sy(p.correctness) + 3} className="fill-foreground text-[10px]">
              {p.name}
            </text>
          </g>
        ))}
      </svg>
      {hover && (
        <div className="mt-1 rounded border bg-popover px-2 py-1 text-xs shadow-sm">
          {(() => {
            const p = points.find((q) => q.name === hover)!;
            return `${p.name}: ${Math.round(p.correctness * 100)}% correct, $${p.cost.toFixed(2)} a task, ${p.runs} runs${best.has(p.name) ? " · on the frontier" : ""}`;
          })()}
        </div>
      )}
    </figure>
  );
}
