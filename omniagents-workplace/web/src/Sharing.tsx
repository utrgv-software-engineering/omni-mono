import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { Copy, Share2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@omniagents/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@omniagents/ui/dialog";
import { Field, FieldLabel } from "@omniagents/ui/field";
import { Input } from "@omniagents/ui/input";
import { Textarea } from "@omniagents/ui/textarea";
import { Toaster } from "@omniagents/ui/sonner";
import { factoryFetch } from "./factory-scope";
import { ArtifactContent } from "./Library";
import { CanvasView } from "./Canvas";
import { canvas, type CanvasDetail, type LibraryArtifact, type Person } from "./api";

export type Feedback = {
  id: string; body: string; author: string; author_id: string; at: number;
  parent_id: string | null; resolved: boolean; guest?: boolean;
};
type Kind = "canvas" | "artifact";
const fail = (e: unknown) => toast.error(e instanceof Error ? e.message : "Request failed");
async function request<T>(path: string, method = "GET", body?: unknown, token?: string): Promise<T> {
  const r = await factoryFetch(path, {
    method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || "Request failed");
  return data;
}

export function ShareButton({ kind, id }: { kind: Kind; id: string }) {
  const [open, setOpen] = useState(false);
  const [links, setLinks] = useState<{ id: string; created_at: number }[]>([]);
  const [created, setCreated] = useState<{ id: string; url: string }>();
  const [busy, setBusy] = useState(false);
  const path = "/workplace/api/sharing/" + kind + "/" + id;
  const load = useCallback(() => request<{ links: typeof links }>(path).then(r => setLinks(r.links)), [path]);
  useEffect(() => { if (open) void load().catch(fail); }, [open, load]);
  return <>
    <Button variant="outline" size="sm" onClick={() => setOpen(true)}><Share2 /> Share</Button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent>
      <DialogHeader><DialogTitle>Share</DialogTitle>
        <DialogDescription>Anyone with the link can view the current content and leave comments.</DialogDescription>
      </DialogHeader>
      {created && <div className="flex gap-2">
        <Input aria-label="Share link" readOnly value={created.url} onFocus={e => e.target.select()} />
        <Button variant="outline" aria-label="Copy share link" onClick={() => void navigator.clipboard.writeText(created.url).then(() => toast.success("Link copied"), fail)}><Copy /></Button>
      </div>}
      <Button disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          const result = await request<{ id: string; url: string }>(path, "POST");
          setCreated({ ...result, url: new URL(result.url, window.location.origin).href });
          await load();
        } catch (e) { fail(e); } finally { setBusy(false); }
      }}>Create link</Button>
      <div className="flex flex-col gap-2">
        {links.map(link => <div key={link.id} className="flex items-center justify-between gap-2 text-sm">
          <span>Created {new Date(link.created_at * 1000).toLocaleString()}</span>
          <Button variant="ghost" size="sm" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              await request(path + "/" + link.id, "DELETE");
              if (created?.id === link.id) setCreated(undefined);
              await load();
            } catch (e) { fail(e); } finally { setBusy(false); }
          }}>Revoke</Button>
        </div>)}
      </div>
    </DialogContent></Dialog>
  </>;
}

export function FeedbackList({ comments, onComment, onResolve, onDelete, account }: {
  comments: Feedback[];
  onComment: (body: { body: string; parent_id?: string }) => Promise<unknown>;
  onResolve?: (id: string, resolved: boolean) => Promise<unknown>;
  onDelete?: (id: string) => Promise<unknown>;
  account?: Pick<Person, "id" | "admin">;
}) {
  const [text, setText] = useState("");
  const [reply, setReply] = useState<string>();
  const [busy, setBusy] = useState(false);
  return <section aria-label="Comments" className="flex min-w-0 flex-col gap-3 p-3">
    <h2 className="text-sm font-semibold">Comments</h2>
    {comments.filter(c => !c.parent_id).map(root => <div key={root.id} className="flex flex-col gap-2 rounded-md border p-3 text-sm">
      {root.resolved && <span className="text-xs text-muted-foreground">Resolved</span>}
      {[root, ...comments.filter(c => c.parent_id === root.id)].map(c => <div key={c.id}>
        <div className="flex items-center gap-2 text-xs">
          <b>{c.author}</b>{c.guest && <span className="text-muted-foreground">Guest</span>}
          <time className="text-muted-foreground">{new Date(c.at * 1000).toLocaleString()}</time>
          {onDelete && (account?.admin || account?.id === c.author_id) && <Button variant="ghost" size="icon-xs" aria-label="Delete comment" onClick={() => void onDelete(c.id).catch(fail)}><Trash2 /></Button>}
        </div>
        <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{c.body}</p>
      </div>)}
      <div className="flex gap-2">
        <Button variant="ghost" size="xs" onClick={() => setReply(root.id)}>Reply</Button>
        {onResolve && <Button variant="ghost" size="xs" onClick={() => void onResolve(root.id, !root.resolved).catch(fail)}>{root.resolved ? "Reopen" : "Resolve"}</Button>}
      </div>
    </div>)}
    <form className="flex flex-col gap-2" onSubmit={async e => {
      e.preventDefault(); setBusy(true);
      try { await onComment({ body: text, ...(reply ? { parent_id: reply } : {}) }); setText(""); setReply(undefined); }
      catch (e) { fail(e); } finally { setBusy(false); }
    }}>
      {reply && <div className="flex items-center gap-2 text-xs">Replying<Button type="button" variant="ghost" size="xs" onClick={() => setReply(undefined)}>Cancel reply</Button></div>}
      <Textarea aria-label={reply ? "Reply" : "Comment"} placeholder={reply ? "Reply…" : "Leave a comment…"} value={text} onChange={e => setText(e.target.value)} required />
      <Button type="submit" disabled={busy || !text.trim()}>{reply ? "Reply" : "Comment"}</Button>
    </form>
  </section>;
}

export function ArtifactFeedback({ id, account, compact = false }: { id: string; account: Person; compact?: boolean }) {
  const [open, setOpen] = useState(!compact);
  const [comments, setComments] = useState<Feedback[]>([]);
  const path = "/workplace/api/library/artifacts/" + id + "/comments";
  const load = useCallback(() => request<{ comments: Feedback[] }>(path).then(r => setComments(r.comments)), [path]);
  useEffect(() => {
    if (!open) return;
    void load().catch(fail);
    const timer = setInterval(() => void load().catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, [open, load]);
  return <div className={compact ? "max-h-[45%] shrink-0 overflow-auto border-t" : ""}>
    {compact && <Button variant="ghost" size="sm" onClick={() => setOpen(!open)}>Comments</Button>}
    {open && <FeedbackList comments={comments} account={account}
      onComment={async body => { await request(path, "POST", body); await load(); }}
      onResolve={async (note, resolved) => { await request(path + "/" + note + "/resolve", "POST", { resolved }); await load(); }}
      onDelete={async note => { await request(path + "/" + note, "DELETE"); await load(); }} />}
  </div>;
}

type Shared = { kind: Kind; item: CanvasDetail | LibraryArtifact; comments: Feedback[]; guest: { id: string; name: string } | null };
export function SharedReview({ id }: { id: string }) {
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("token") || "");
  const [data, setData] = useState<Shared>();
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [naming, setNaming] = useState(false);
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identityError, setIdentityError] = useState("");
  const pending = useRef<{ resolve: () => void; reject: (e: Error) => void } | undefined>(undefined);
  const guest = useRef<Shared["guest"]>(null);
  const [revision, setRevision] = useState(0);
  const path = "/share/" + id;
  const read = useCallback(async () => {
    const next = await request<Shared>(path + "/api", "GET", undefined, token);
    setData(next); setRevision(v => v + 1); guest.current = next.guest; setError("");
    return next;
  }, [path, token]);
  useEffect(() => {
    if (!token) { setError("This share link is incomplete."); return; }
    void read().catch(e => setError(e.message));
    const timer = setInterval(() => void read().catch(e => { setData(undefined); setError(e.message); }), 5000);
    return () => { clearInterval(timer); pending.current?.reject(new Error("Review closed")); pending.current = undefined; };
  }, [read, token]);
  const comment = useCallback(async (body: Parameters<typeof canvas.comment>[1]) => {
    if (!guest.current) {
      await new Promise<void>((resolve, reject) => { pending.current = { resolve, reject }; setNaming(true); });
    }
    const note = await request<any>(path + "/comments", "POST", body, token);
    await read();
    return note;
  }, [path, token, read]);
  const client = useMemo(() => ({
    ...canvas,
    get: async () => (await request<Shared>(path + "/api", "GET", undefined, token)).item as CanvasDetail,
    comment: async (_id: string, body: Parameters<typeof canvas.comment>[1]) => comment(body),
  }), [path, token, comment]);
  if (error) return <main className="p-6" role="alert">{error}</main>;
  if (!data) return <main className="p-6">Loading…</main>;
  const account = { id: data.guest?.id || "", name: data.guest?.name || "", admin: false } as Person;
  return <div className="flex h-dvh min-h-0 flex-col">
    {data.kind === "canvas"
      ? <CanvasView id={data.item.id} account={account} version={revision} navigate={() => undefined} client={client} readOnly />
      : <><header className="border-b p-4 font-semibold">{data.item.title}</header>
        <div className="flex min-h-0 flex-1 flex-col overflow-auto md:flex-row">
          <main className="min-w-0 flex-1 p-3"><ArtifactContent artifact={data.item as LibraryArtifact} /></main>
          <aside className="shrink-0 border-t md:w-80 md:overflow-auto md:border-l md:border-t-0"><FeedbackList comments={data.comments} onComment={comment} /></aside>
        </div></>}
    <Dialog open={naming} onOpenChange={open => {
      if (!open) { pending.current?.reject(new Error("Comment cancelled")); pending.current = undefined; setNaming(false); }
    }}><DialogContent><DialogHeader><DialogTitle>Your name</DialogTitle>
      <DialogDescription>Your name will appear beside your comments.</DialogDescription></DialogHeader>
      <form className="flex flex-col gap-3" onSubmit={async e => {
        e.preventDefault(); setIdentityBusy(true); setIdentityError("");
        try {
          guest.current = await request(path + "/identity", "POST", { name }, token);
          setNaming(false); pending.current?.resolve(); pending.current = undefined;
        } catch (e) { setIdentityError(e instanceof Error ? e.message : "Unable to save your name"); }
        finally { setIdentityBusy(false); }
      }}>
        <Field><FieldLabel htmlFor="guest-name">Name</FieldLabel><Input id="guest-name" autoFocus required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field>
        {identityError && <p role="alert" className="text-sm">{identityError}</p>}
        <Button type="submit" disabled={identityBusy || !name.trim()}>Continue</Button>
      </form>
    </DialogContent></Dialog>
    <Toaster />
  </div>;
}
