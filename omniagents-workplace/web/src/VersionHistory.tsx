import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@omniagents/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { factoryFetch } from "./factory-scope";
import { API, json, savedMessage, send, type Saved } from "./definition-api";

type Commit = { sha: string; message: string; author: string; date: string };

/** A file's versions, each with its change, and putting an old one back. */
export function VersionHistory({ path, title, onClose, onRestored }: { path: string; title: string; onClose: () => void; onRestored: () => void }) {
  const [commits, setCommits] = useState<Commit[]>();
  const [shown, setShown] = useState<{ sha: string; diff: string }>();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void factoryFetch(`${API}/history?path=${encodeURIComponent(path)}`)
      .then(r => json<{ commits: Commit[] }>(r, "Unable to load the history"))
      .then(body => setCommits(body.commits))
      .catch(e => toast.error(e instanceof Error ? e.message : "Unable to load the history"));
  }, [path]);
  const show = async (sha: string) => {
    if (shown?.sha === sha) return setShown(undefined);
    try {
      const body = await factoryFetch(`${API}/diff?path=${encodeURIComponent(path)}&commit=${sha}`).then(r => json<{ diff: string }>(r, "Unable to load the change"));
      setShown({ sha, diff: body.diff });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to load the change");
    }
  };
  const restore = async (sha: string) => {
    setBusy(true);
    try {
      const saved = await send<Saved>("/restore", "POST", { path, commit: sha }, "Unable to restore");
      const message = savedMessage(saved, "Restored. The factory uses it from its next run.");
      toast[message.kind](message.text);
      onRestored();
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to restore");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={open => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Version history</DialogTitle>
          <DialogDescription>{title}</DialogDescription>
        </DialogHeader>
        {!commits ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <ol className="max-h-[60vh] divide-y overflow-y-auto">
            {commits.map((c, index) => (
              <li key={c.sha} className="flex flex-col gap-2 py-3 text-sm">
                <div className="flex items-start gap-3">
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => void show(c.sha)}>
                    <span className="block font-medium hover:underline">{c.message}</span>
                    <span className="text-xs text-muted-foreground">
                      {c.author} · {new Date(c.date).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                      {index === 0 && " · current version"}
                    </span>
                  </button>
                  {index > 0 && (
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => void restore(c.sha)}>
                      Restore
                    </Button>
                  )}
                </div>
                {shown?.sha === c.sha && (
                  <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-xs">{shown.diff || "No change to this file."}</pre>
                )}
              </li>
            ))}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}
