// Canvases: design boards laid out as their index places them, live (each
// board a sandboxed page), with their version history and comments pinned
// to boards.
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { Textarea } from "@omniagents/ui/textarea";
import {
  ExternalLink,
  History,
  Maximize2,
  MessageCircle,
  Minus,
  Move,
  MousePointer2,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  canvas as api,
  type CanvasComment,
  type CanvasDetail,
  type CanvasSummary,
  type Person,
} from "./api";

export interface CanvasRoute {
  id?: string;
}

export function parseCanvasPath(pathname: string): CanvasRoute | null {
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] !== "canvas") return null;
  return { id: parts[1] };
}

const fail = (e: unknown) => {
  toast.error((e as Error).message);
};

export function CanvasApp({
  route,
  account,
  version,
  navigate,
}: {
  route: CanvasRoute;
  account: Person;
  version: number;
  navigate: (path: string) => void;
}) {
  return route.id ? (
    <CanvasView
      id={route.id}
      account={account}
      version={version}
      navigate={navigate}
    />
  ) : (
    <CanvasList version={version} navigate={navigate} />
  );
}

function CanvasList({
  version,
  navigate,
}: {
  version: number;
  navigate: (path: string) => void;
}) {
  const [items, setItems] = useState<CanvasSummary[] | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  useEffect(() => {
    void api.list().then((r) => setItems(r.canvases), fail);
  }, [version]);
  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 overflow-y-auto p-4 md:p-6">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Canvases</h1>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          onClick={() => setTitle("")}
        >
          <Plus /> New canvas
        </Button>
      </div>
      {title !== null && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void api
              .create(title)
              .then((c) => navigate(`/canvas/${c.id}`), fail);
          }}
        >
          <Input
            autoFocus
            placeholder="Onboarding redesign"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <Button type="submit" disabled={!title.trim()}>
            Create
          </Button>
          <Button type="button" variant="ghost" onClick={() => setTitle(null)}>
            Cancel
          </Button>
        </form>
      )}
      {items && items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No canvases yet. Agents make them through the canvas MCP server (its
          URL and token are in Apps and Your tokens), or make one here.
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(items ?? []).map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => navigate(`/canvas/${c.id}`)}
            className="rounded-lg border p-4 text-left hover:bg-accent"
          >
            <div className="font-semibold">{c.title}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {c.boards} {c.boards === 1 ? "board" : "boards"} · version{" "}
              {c.version} · {c.owner}
            </div>
            <div className="text-xs text-muted-foreground">
              Updated {new Date(c.updated_at * 1000).toLocaleString()}
              {c.open_comments > 0 && ` · ${c.open_comments} open comments`}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

type Mode = "interact" | "move" | "comment";

function CanvasView({
  id,
  account,
  version,
  navigate,
}: {
  id: string;
  account: Person;
  version: number;
  navigate: (path: string) => void;
}) {
  const [detail, setDetail] = useState<CanvasDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [shown, setShown] = useState<number | undefined>(undefined);
  const [zoom, setZoom] = useState(0.25);
  const [pan, setPan] = useState({ x: 40, y: 80 });
  const [mode, setMode] = useState<Mode>("interact");
  const [panel, setPanel] = useState<"comments" | "history" | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [draft, setDraft] = useState<{
    board: string;
    x: number;
    y: number;
    text: string;
  } | null>(null);
  const [editingTitle, setEditingTitle] = useState<string | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const fitted = useRef(false);

  const load = useCallback(
    () =>
      api.get(id, shown).then(
        (d) => {
          setDetail(d);
          setMissing(false);
        },
        () => setMissing(true),
      ),
    [id, shown],
  );
  useEffect(() => {
    void load();
  }, [load, version]);

  // Fit the boards in view the first time.
  useEffect(() => {
    if (!detail || fitted.current || !surface.current) return;
    const boards = Object.values(detail.index.boards);
    if (!boards.length) return;
    fitted.current = true;
    const minX = Math.min(...boards.map((b) => b.x));
    const minY = Math.min(...boards.map((b) => b.y));
    const maxX = Math.max(...boards.map((b) => b.x + b.w));
    const maxY = Math.max(...boards.map((b) => b.y + b.h));
    const { width, height } = surface.current.getBoundingClientRect();
    const z = Math.min(
      1,
      (width - 80) / (maxX - minX),
      (height - 120) / (maxY - minY),
    );
    setZoom(Math.max(0.05, z));
    setPan({ x: 40 - minX * z, y: 80 - minY * z });
  }, [detail]);

  // Panning with a drag on the background, zooming with the wheel (around the pointer).
  const dragging = useRef<{
    x: number;
    y: number;
    px: number;
    py: number;
  } | null>(null);
  const onWheel = (e: React.WheelEvent) => {
    if (!surface.current) return;
    const rect = surface.current.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const next = Math.min(
      2,
      Math.max(0.05, zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1)),
    );
    setPan({
      x: mx - ((mx - pan.x) * next) / zoom,
      y: my - ((my - pan.y) * next) / zoom,
    });
    setZoom(next);
  };

  if (missing)
    return (
      <div className="p-6 text-sm text-muted-foreground">No such canvas.</div>
    );
  if (!detail)
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;

  const current = detail.shown === detail.version;
  const index = detail.index;
  const roots = detail.comments.filter((c) => !c.parent_id);
  const openRoots = roots.filter((c) => !c.resolved);

  const moveBoard = (name: string, x: number, y: number) => {
    const next = structuredClone(index);
    next.boards[name] = {
      ...next.boards[name],
      x: Math.round(x),
      y: Math.round(y),
    };
    setDetail({ ...detail, index: next });
    void api
      .publish(detail.id, {
        files: { "canvas.json": JSON.stringify(next, null, 2) },
        base_version: detail.version,
        label: `Moved ${next.boards[name].title ?? name}`,
      })
      .then(load, (e) => {
        fail(e);
        void load();
      });
  };

  return (
    <div className="relative flex min-h-0 flex-1">
      <div className="relative min-w-0 flex-1 overflow-hidden bg-muted/40">
        <div className="absolute top-3 left-3 z-20 flex flex-wrap items-center gap-2 rounded-lg border bg-background/95 p-1.5 shadow-sm max-md:right-3">
          <Button size="xs" variant="ghost" onClick={() => navigate("/canvas")}>
            ← Library
          </Button>
          {editingTitle !== null ? (
            <form
              className="flex gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                void api.rename(detail.id, editingTitle).then(() => {
                  setEditingTitle(null);
                  void load();
                }, fail);
              }}
            >
              <Input
                autoFocus
                className="h-7 w-64 max-md:w-40"
                value={editingTitle}
                onChange={(e) => setEditingTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && setEditingTitle(null)}
              />
              <Button size="xs" type="submit">
                Save
              </Button>
            </form>
          ) : (
            <button
              type="button"
              className="px-1 font-semibold hover:underline"
              onClick={() => current && setEditingTitle(detail.title)}
              title={current ? "Rename" : undefined}
            >
              {detail.title}
            </button>
          )}
          <span className="text-xs text-muted-foreground">
            {current
              ? `version ${detail.version}`
              : `viewing version ${detail.shown} of ${detail.version}`}
          </span>
          {!current && (
            <>
              <Button
                size="xs"
                variant="outline"
                onClick={() => setShown(undefined)}
              >
                Back to current
              </Button>
              <Button
                size="xs"
                onClick={() =>
                  void api.restore(detail.id, detail.shown).then(() => {
                    setShown(undefined);
                  }, fail)
                }
              >
                <RotateCcw /> Restore this version
              </Button>
            </>
          )}
          <span className="mx-1 h-5 w-px bg-border" />
          {(
            [
              ["interact", MousePointer2, "Use the boards"],
              ["move", Move, "Move boards"],
              ["comment", MessageCircle, "Comment on a board"],
            ] as const
          ).map(([m, Icon, title]) => (
            <Button
              key={m}
              size="icon-xs"
              variant={mode === m ? "secondary" : "ghost"}
              title={title}
              disabled={m !== "interact" && !current}
              onClick={() => setMode(m)}
            >
              <Icon />
            </Button>
          ))}
          <span className="mx-1 h-5 w-px bg-border" />
          <Button
            size="icon-xs"
            variant="ghost"
            title="Zoom out"
            onClick={() => setZoom((z) => Math.max(0.05, z / 1.25))}
          >
            <Minus />
          </Button>
          <span className="w-10 text-center text-xs">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            size="icon-xs"
            variant="ghost"
            title="Zoom in"
            onClick={() => setZoom((z) => Math.min(2, z * 1.25))}
          >
            <Plus />
          </Button>
          <span className="mx-1 h-5 w-px bg-border" />
          <Button
            size="xs"
            variant={panel === "comments" ? "secondary" : "ghost"}
            onClick={() => setPanel(panel === "comments" ? null : "comments")}
          >
            <MessageCircle /> {openRoots.length}
          </Button>
          <Button
            size="xs"
            variant={panel === "history" ? "secondary" : "ghost"}
            onClick={() => setPanel(panel === "history" ? null : "history")}
          >
            <History /> History
          </Button>
          {(detail.owner_id === account.id || account.admin) && (
            <Button
              size="icon-xs"
              variant="ghost"
              title="Delete the canvas"
              onClick={() => {
                if (window.confirm(`Delete "${detail.title}" and its history?`))
                  void api
                    .remove(detail.id)
                    .then(() => navigate("/canvas"), fail);
              }}
            >
              <Trash2 />
            </Button>
          )}
        </div>

        <div
          ref={surface}
          className={`absolute inset-0 ${dragging.current ? "cursor-grabbing" : "cursor-grab"}`}
          onWheel={onWheel}
          onPointerDown={(e) => {
            if (e.target !== e.currentTarget) return;
            dragging.current = {
              x: e.clientX,
              y: e.clientY,
              px: pan.x,
              py: pan.y,
            };
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            const d = dragging.current;
            if (d)
              setPan({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y });
          }}
          onPointerUp={() => {
            dragging.current = null;
          }}
        >
          <div
            className="pointer-events-none absolute top-0 left-0 origin-top-left"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            }}
          >
            {Object.entries(index.notes ?? {}).map(([key, note]) => (
              <div
                key={key}
                className="absolute text-foreground"
                style={{
                  left: note.x,
                  top: note.y,
                  // As wide as its text, up to maxW (its layer has no width to wrap in).
                  width: "max-content",
                  minWidth: note.w,
                  maxWidth: note.maxW ?? note.w,
                  fontSize: note.kind === "title1" ? 64 : 32,
                  fontWeight: note.kind?.startsWith("title") ? 600 : 400,
                  lineHeight: 1.2,
                }}
              >
                {note.text}
              </div>
            ))}
            {index.order.map((name) => {
              const board = index.boards[name];
              if (!board) return null;
              return (
                <Board
                  key={name}
                  name={name}
                  board={board}
                  src={`${detail.view}${encodeURIComponent(name)}`}
                  zoom={zoom}
                  mode={current ? mode : "interact"}
                  comments={openRoots.filter((c) => c.board === name)}
                  onMove={(x, y) => moveBoard(name, x, y)}
                  onComment={(x, y) =>
                    setDraft({ board: name, x, y, text: "" })
                  }
                  onExpand={() => setExpanded(name)}
                  onOpenComments={() => setPanel("comments")}
                />
              );
            })}
          </div>
        </div>
        {Object.keys(index.boards).length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
            No boards yet. An agent adds them by publishing (see the canvas MCP
            server's publish tool).
          </div>
        )}
        {draft && (
          <div className="absolute right-4 bottom-4 z-30 w-80 space-y-2 rounded-lg border bg-background p-3 shadow-lg max-md:left-4 max-md:w-auto">
            <div className="text-xs text-muted-foreground">
              Comment on {index.boards[draft.board]?.title ?? draft.board}
            </div>
            <Textarea
              autoFocus
              rows={3}
              placeholder="Make the header darker"
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
            />
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={!draft.text.trim()}
                onClick={() =>
                  void api
                    .comment(detail.id, {
                      body: draft.text,
                      board: draft.board,
                      x: draft.x,
                      y: draft.y,
                    })
                    .then(() => {
                      setDraft(null);
                      setPanel("comments");
                      void load();
                    }, fail)
                }
              >
                Comment
              </Button>
            </div>
          </div>
        )}
        {expanded && index.boards[expanded] && (
          <div className="absolute inset-0 z-40 flex flex-col bg-background">
            <div className="flex items-center gap-2 border-b px-3 py-2 text-sm">
              <span className="font-semibold">
                {index.boards[expanded].title ?? expanded}
              </span>
              <a
                href={`${detail.view}${encodeURIComponent(expanded)}`}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 text-xs text-sky-600 hover:underline"
              >
                <ExternalLink className="size-3" /> Open in a tab
              </a>
              <Button
                size="icon-sm"
                variant="ghost"
                className="ml-auto"
                onClick={() => setExpanded(null)}
              >
                <X />
              </Button>
            </div>
            <iframe
              title={expanded}
              src={`${detail.view}${encodeURIComponent(expanded)}`}
              sandbox="allow-scripts"
              className="min-h-0 flex-1 border-0 bg-white"
            />
          </div>
        )}
      </div>

      {panel && (
        // Phones: the panel covers the canvas until it is closed.
        <aside className="flex w-80 shrink-0 flex-col border-l bg-background max-md:absolute max-md:inset-0 max-md:z-40 max-md:w-full max-md:border-l-0">
          <div className="flex items-center border-b px-3 py-2 text-sm font-semibold">
            {panel === "comments" ? "Comments" : "History"}
            <Button
              size="icon-xs"
              variant="ghost"
              className="ml-auto"
              onClick={() => setPanel(null)}
            >
              <X />
            </Button>
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 text-sm">
            {panel === "history" ? (
              [...detail.versions].reverse().map((v) => (
                <button
                  key={v.version}
                  type="button"
                  onClick={() =>
                    setShown(
                      v.version === detail.version ? undefined : v.version,
                    )
                  }
                  className={`block w-full rounded-md border p-2 text-left hover:bg-accent ${detail.shown === v.version ? "border-primary" : ""}`}
                >
                  <div className="font-medium">
                    Version {v.version}
                    {v.label && ` · ${v.label}`}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {v.author} · {new Date(v.at * 1000).toLocaleString()}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {v.changed.join(", ")}
                  </div>
                </button>
              ))
            ) : (
              <>
                {roots.length === 0 && (
                  <p className="text-muted-foreground">
                    No comments. Pick the comment tool and click a board.
                  </p>
                )}
                {roots.map((root) => (
                  <Thread
                    key={root.id}
                    root={root}
                    replies={detail.comments.filter(
                      (c) => c.parent_id === root.id,
                    )}
                    boardTitle={
                      root.board
                        ? (index.boards[root.board]?.title ?? root.board)
                        : null
                    }
                    account={account}
                    canvasId={detail.id}
                    onChanged={() => void load()}
                  />
                ))}
              </>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}

function Board({
  name,
  board,
  src,
  zoom,
  mode,
  comments,
  onMove,
  onComment,
  onExpand,
  onOpenComments,
}: {
  name: string;
  board: CanvasDetail["index"]["boards"][string];
  src: string;
  zoom: number;
  mode: Mode;
  comments: CanvasComment[];
  onMove: (x: number, y: number) => void;
  onComment: (x: number, y: number) => void;
  onExpand: () => void;
  onOpenComments: () => void;
}) {
  const [offset, setOffset] = useState<{ x: number; y: number } | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const x = board.x + (offset?.x ?? 0);
  const y = board.y + (offset?.y ?? 0);
  return (
    <div
      className="pointer-events-auto absolute"
      style={{ left: x, top: y, width: board.w, height: board.h }}
    >
      <div
        className={`absolute left-0 flex items-center gap-3 leading-none whitespace-nowrap text-muted-foreground ${mode === "move" ? "cursor-move" : ""}`}
        // Above the board whatever its size: it grows as the canvas zooms out.
        style={{
          bottom: "100%",
          marginBottom: Math.min(32, 8 / zoom),
          fontSize: Math.min(64, 14 / zoom),
        }}
        onPointerDown={(e) => {
          if (mode !== "move") return;
          start.current = { x: e.clientX, y: e.clientY };
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!start.current) return;
          setOffset({
            x: (e.clientX - start.current.x) / zoom,
            y: (e.clientY - start.current.y) / zoom,
          });
        }}
        onPointerUp={() => {
          if (start.current && offset)
            onMove(board.x + offset.x, board.y + offset.y);
          start.current = null;
          setOffset(null);
        }}
      >
        {board.title ?? name}
        <button
          type="button"
          title="Open this board"
          onClick={onExpand}
          className="pointer-events-auto"
        >
          <Maximize2
            style={{
              width: Math.min(48, 12 / zoom),
              height: Math.min(48, 12 / zoom),
            }}
          />
        </button>
      </div>
      <iframe
        title={board.title ?? name}
        src={src}
        sandbox="allow-scripts"
        loading="lazy"
        className="h-full w-full rounded-sm border-0 bg-white shadow-md"
        style={{ pointerEvents: mode === "interact" ? "auto" : "none" }}
      />
      {mode !== "interact" && (
        <div
          className={`absolute inset-0 ${mode === "comment" ? "cursor-crosshair" : "cursor-move"}`}
          onPointerDown={(e) => {
            if (mode !== "move") return;
            start.current = { x: e.clientX, y: e.clientY };
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (mode !== "move" || !start.current) return;
            setOffset({
              x: (e.clientX - start.current.x) / zoom,
              y: (e.clientY - start.current.y) / zoom,
            });
          }}
          onPointerUp={(e) => {
            if (mode === "comment") {
              const rect = (
                e.currentTarget as HTMLElement
              ).getBoundingClientRect();
              onComment(
                Math.round((e.clientX - rect.left) / zoom),
                Math.round((e.clientY - rect.top) / zoom),
              );
              return;
            }
            if (start.current && offset)
              onMove(board.x + offset.x, board.y + offset.y);
            start.current = null;
            setOffset(null);
          }}
        />
      )}
      {comments.map((c, n) =>
        c.x === null || c.y === null ? null : (
          <button
            key={c.id}
            type="button"
            title={`${c.author}: ${c.body}`}
            onClick={onOpenComments}
            className="absolute flex items-center justify-center rounded-full rounded-bl-none bg-indigo-600 font-semibold text-white shadow"
            style={{
              left: c.x,
              top: c.y,
              width: 28 / zoom,
              height: 28 / zoom,
              fontSize: 13 / zoom,
              transform: "translateY(-100%)",
            }}
          >
            {n + 1}
          </button>
        ),
      )}
    </div>
  );
}

function Thread({
  root,
  replies,
  boardTitle,
  account,
  canvasId,
  onChanged,
}: {
  root: CanvasComment;
  replies: CanvasComment[];
  boardTitle: string | null;
  account: Person;
  canvasId: string;
  onChanged: () => void;
}) {
  const [text, setText] = useState("");
  return (
    <div
      className={`space-y-2 rounded-md border p-2 ${root.resolved ? "opacity-60" : ""}`}
    >
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {boardTitle ?? "The canvas"}
        {root.resolved && " · resolved"}
        <Button
          size="xs"
          variant="ghost"
          className="ml-auto"
          onClick={() =>
            void api.resolve(root.id, !root.resolved).then(onChanged, fail)
          }
        >
          {root.resolved ? "Reopen" : "Resolve"}
        </Button>
      </div>
      {[root, ...replies].map((c) => (
        <div key={c.id} className="group">
          <div className="flex items-center gap-1 text-xs">
            <b>{c.author}</b>
            <span className="text-muted-foreground">
              {new Date(c.at * 1000).toLocaleString()} · v{c.version}
            </span>
            {(c.author_id === account.id || account.admin) && (
              <button
                type="button"
                title="Delete"
                className="ml-auto hidden group-hover:block"
                onClick={() =>
                  void api.deleteComment(c.id).then(onChanged, fail)
                }
              >
                <Trash2 className="size-3 text-destructive" />
              </button>
            )}
          </div>
          <div className="whitespace-pre-wrap">{c.body}</div>
        </div>
      ))}
      <form
        className="flex gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          void api
            .comment(canvasId, { body: text, parent_id: root.id })
            .then(() => {
              setText("");
              onChanged();
            }, fail);
        }}
      >
        <Input
          className="h-7 text-xs"
          placeholder="Reply"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <Button size="xs" type="submit" disabled={!text.trim()}>
          Reply
        </Button>
      </form>
    </div>
  );
}
