import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ArrowLeft,
  BookOpen,
  FileText,
  Globe,
  LayoutDashboard,
  Plus,
  RefreshCw,
  Trash2,
  Pencil,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@omniagents/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@omniagents/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@omniagents/ui/dialog";
import { Input } from "@omniagents/ui/input";
import { Field, FieldLabel, FieldGroup } from "@omniagents/ui/field";
import { Tabs, TabsList, TabsTrigger } from "@omniagents/ui/tabs";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
  EmptyMedia,
} from "@omniagents/ui/empty";
import { Skeleton } from "@omniagents/ui/skeleton";
import { canvas, library, type LibraryArtifact, type Person } from "./api";
import "./library.css";

type Entry = {
  id: string;
  title: string;
  kind: "canvas" | "page" | "document";
  owner_id: string;
  updated_at: number;
  preview?: { src: string; width: number; height: number } | null;
  markdown?: string;
};
const labels = { canvas: "Canvas", page: "Page", document: "Document" };
const icons = { canvas: LayoutDashboard, page: Globe, document: FileText };
const fail = (error: unknown) =>
  toast.error(
    error instanceof Error ? error.message : "Unable to load Library",
  );

function Preview({ entry }: { entry: Entry }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(300);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([size]) =>
      setWidth(size.contentRect.width),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const sourceWidth = Math.max(320, entry.preview?.width || 960);
  const scale = width / sourceWidth;
  return (
    <div
      ref={ref}
      className="library-thumbnail"
      aria-label={`Preview of ${entry.title}`}
      role="img"
    >
      <div
        inert
        className="library-thumbnail-content"
        style={{
          width: sourceWidth,
          height: Math.max(
            entry.preview?.height || 600,
            (width * 0.625) / scale,
          ),
          transform: `scale(${scale})`,
        }}
      >
        {entry.kind === "document" ? (
          <article className="library-document">
            <ReactMarkdown>
              {entry.markdown || `# ${entry.title}`}
            </ReactMarkdown>
          </article>
        ) : entry.preview ? (
          <iframe
            title={`${entry.title} thumbnail`}
            src={entry.preview.src}
            sandbox="allow-scripts"
            tabIndex={-1}
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="library-blank-canvas">
            <LayoutDashboard />
            <h2>{entry.title}</h2>
            <p>No boards yet</p>
          </div>
        )}
      </div>
    </div>
  );
}

export function LibraryApp({
  id,
  account,
  version,
  navigate,
}: {
  id?: string;
  account: Person;
  version: number;
  navigate: (path: string) => void;
}) {
  const [items, setItems] = useState<Entry[] | null>(null);
  const [opened, setOpened] = useState<LibraryArtifact | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<{
    action: "create" | "rename" | "delete";
    entry?: Entry;
  } | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<Entry["kind"]>("document");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setError("");
    try {
      if (id) {
        setOpened(await library.get(id));
        return;
      }
      const [canvases, artifacts] = await Promise.all([
        canvas.list(),
        library.list(),
      ]);
      setItems(
        [
          ...canvases.canvases.map((c) => ({ ...c, kind: "canvas" as const })),
          ...artifacts.artifacts.map((a) => ({
            ...a,
            preview:
              a.kind === "page"
                ? { src: a.view, width: 960, height: 600 }
                : null,
          })),
        ].sort((a, b) => b.updated_at - a.updated_at),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load Library");
    }
  }, [id]);
  useEffect(() => {
    setOpened(null);
    void load();
  }, [load, version]);
  function open(entry: Entry) {
    navigate(
      entry.kind === "canvas" ? `/canvas/${entry.id}` : `/library/${entry.id}`,
    );
  }
  async function save() {
    if (!dialog) return;
    setBusy(true);
    try {
      const entry = dialog.entry;
      if (dialog.action === "delete" && entry) {
        await (entry.kind === "canvas"
          ? canvas.remove(entry.id)
          : library.delete(entry.id));
        if (id) {
          setDialog(null);
          navigate("/library");
          return;
        }
      } else if (dialog.action === "rename" && entry) {
        await (entry.kind === "canvas"
          ? canvas.rename(entry.id, title)
          : library.rename(entry.id, title));
      } else {
        const created =
          kind === "canvas"
            ? await canvas.create(title)
            : await library.create(title, kind);
        open({ ...created, kind });
      }
      setDialog(null);
      await load();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  const actions = (entry: Entry) => (
    <>
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Rename ${entry.title}`}
        onClick={() => {
          setTitle(entry.title);
          setDialog({ action: "rename", entry });
        }}
      >
        <Pencil />
      </Button>
      {(account.admin || entry.owner_id === account.id) && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete ${entry.title}`}
          onClick={() => setDialog({ action: "delete", entry })}
        >
          <Trash2 />
        </Button>
      )}
    </>
  );
  const shown = (items || []).filter(
    (e) =>
      (filter === "all" || e.kind === filter) &&
      e.title.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 md:p-8">
        <header className="flex flex-wrap items-center gap-3">
          {id && (
            <Button
              variant="ghost"
              size="icon"
              aria-label="Back to Library"
              onClick={() => navigate("/library")}
            >
              <ArrowLeft />
            </Button>
          )}
          <div className="mr-auto">
            <h1 className="text-2xl font-semibold tracking-tight">
              {id ? opened?.title || "Opening artifact…" : "Library"}
            </h1>
            {!id && (
              <p className="mt-1 text-sm text-muted-foreground">
                Canvases, pages, and documents you can come back to.
              </p>
            )}
          </div>
          {opened && actions(opened)}
          <Button
            variant="outline"
            size="icon"
            aria-label="Refresh Library"
            onClick={() => void load()}
          >
            <RefreshCw />
          </Button>
          {!id && (
            <Button
              onClick={() => {
                setTitle("");
                setDialog({ action: "create" });
              }}
            >
              <Plus data-icon="inline-start" />
              New
            </Button>
          )}
        </header>
        {error ? (
          <div role="alert">
            {error}
            <Button variant="link" onClick={() => void load()}>
              Retry
            </Button>
          </div>
        ) : id ? (
          opened ? (
            opened.kind === "page" ? (
              <iframe
                key={opened.view + opened.updated_at}
                className="library-page"
                title={opened.title}
                src={opened.view}
                sandbox="allow-scripts"
                referrerPolicy="no-referrer"
              />
            ) : (
              <article className="library-document library-document-full">
                <ReactMarkdown>{opened.markdown}</ReactMarkdown>
              </article>
            )
          ) : (
            <Skeleton className="h-96 w-full" />
          )
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Tabs value={filter} onValueChange={setFilter}>
                <TabsList aria-label="Library types">
                  <TabsTrigger value="all">All</TabsTrigger>
                  <TabsTrigger value="canvas">Canvases</TabsTrigger>
                  <TabsTrigger value="page">Pages</TabsTrigger>
                  <TabsTrigger value="document">Documents</TabsTrigger>
                </TabsList>
              </Tabs>
              <Input
                className="w-full sm:w-64"
                aria-label="Search Library"
                placeholder="Search Library…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            {!items ? (
              <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                {[1, 2, 3].map((n) => (
                  <Skeleton key={n} className="h-72 w-full" />
                ))}
              </div>
            ) : !shown.length ? (
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <BookOpen />
                  </EmptyMedia>
                  <EmptyTitle>
                    {items.length ? "No matches" : "Your Library starts here"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {items.length
                      ? "Try another search or type."
                      : "Ask Omni to create a page, document, or canvas. It will be saved here."}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                {shown.map((entry) => {
                  const Icon = icons[entry.kind];
                  return (
                    <Card
                      key={`${entry.kind}:${entry.id}`}
                      className="gap-0 overflow-hidden py-0"
                    >
                      <CardContent className="relative p-0">
                        <Preview entry={entry} />
                        <Button
                          variant="ghost"
                          className="absolute inset-0 h-full w-full rounded-none"
                          aria-label={`Open ${entry.title}`}
                          onClick={() => open(entry)}
                        />
                      </CardContent>
                      <CardHeader className="gap-2 px-4 pt-4">
                        <CardTitle>
                          <button
                            className="text-left"
                            onClick={() => open(entry)}
                          >
                            {entry.title}
                          </button>
                        </CardTitle>
                        <CardDescription className="flex items-center gap-2">
                          <Icon className="size-3.5" />
                          {labels[entry.kind]}
                        </CardDescription>
                      </CardHeader>
                      <CardFooter className="justify-between px-4 pb-3 pt-2">
                        <span className="text-xs text-muted-foreground">
                          {new Date(
                            entry.updated_at * 1000,
                          ).toLocaleDateString()}
                        </span>
                        <div className="flex">{actions(entry)}</div>
                      </CardFooter>
                    </Card>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
      <Dialog
        open={!!dialog}
        onOpenChange={(value) => !value && !busy && setDialog(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {dialog?.action === "delete"
                ? `Delete ${dialog.entry?.title}?`
                : dialog?.action === "rename"
                  ? "Rename"
                  : "New in Library"}
            </DialogTitle>
            <DialogDescription>
              {dialog?.action === "delete"
                ? "This removes the artifact and its files. This cannot be undone."
                : "Give it a name you’ll recognize."}
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
            className="flex flex-col gap-5"
          >
            {dialog?.action !== "delete" && (
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="library-title">Title</FieldLabel>
                  <Input
                    id="library-title"
                    autoFocus
                    maxLength={120}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    required
                  />
                </Field>
                {dialog?.action === "create" && (
                  <Field>
                    <FieldLabel htmlFor="library-kind">Type</FieldLabel>
                    <select
                      id="library-kind"
                      className="rounded-md border bg-background p-2"
                      value={kind}
                      onChange={(e) => setKind(e.target.value as Entry["kind"])}
                    >
                      <option value="document">Document</option>
                      <option value="page">Page</option>
                      <option value="canvas">Canvas</option>
                    </select>
                  </Field>
                )}
              </FieldGroup>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setDialog(null)}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant={
                  dialog?.action === "delete" ? "destructive" : "default"
                }
                disabled={
                  busy || (dialog?.action !== "delete" && !title.trim())
                }
              >
                {busy
                  ? "Saving…"
                  : dialog?.action === "delete"
                    ? "Delete"
                    : "Save"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
