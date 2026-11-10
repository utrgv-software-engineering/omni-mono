// The kill switch: stop all of a factory's AI work, or every factory's, and
// resume it. Stopping and resuming need a reason; the server records who and why.
import { useCallback, useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@omniagents/ui/dialog";
import { toast } from "sonner";
import { factoryStop, type StopView } from "./api";

export function useFactoryStop(interval = 10000) {
  const [view, setView] = useState<StopView | null>(null);
  const load = useCallback(
    () => factoryStop.get().then(setView, () => undefined),
    [],
  );
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), interval);
    return () => window.clearInterval(timer);
  }, [load, interval]);
  return { view, reload: load };
}

/** Asks for the reason, then stops or resumes. */
export function StopDialog({
  open,
  onOpenChange,
  resuming,
  scope,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resuming: boolean;
  scope: "factory" | "installation";
  onSubmit: (reason: string) => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const what = scope === "installation" ? "all factories" : "this factory";
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setReason("");
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            onSubmit(reason.trim())
              .then(() => {
                setReason("");
                onOpenChange(false);
              })
              .catch((e: unknown) => toast.error((e as Error).message))
              .finally(() => setBusy(false));
          }}
        >
          <DialogHeader>
            <DialogTitle>{resuming ? `Resume ${what}` : `Stop ${what}`}</DialogTitle>
            <DialogDescription>
              {resuming
                ? "Agents start taking work again. Nothing received while stopped is replayed."
                : "Every agent stops now: running work is cancelled, queued work is removed, and nothing new starts until it is resumed. Conversations and files are kept."}
            </DialogDescription>
          </DialogHeader>
          <label className="my-4 block space-y-1">
            <span className="text-sm font-medium">Reason</span>
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              required
              autoFocus
            />
          </label>
          <DialogFooter>
            <Button
              type="submit"
              variant={resuming ? "default" : "destructive"}
              disabled={busy || !reason.trim()}
            >
              {resuming ? "Resume" : "Stop"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The Factory tab's control: Stop, or what stopped it and Resume. */
export function FactoryStopControl() {
  const { view, reload } = useFactoryStop();
  const [open, setOpen] = useState(false);
  if (!view) return null;
  const stopped = !!view.stopped;
  // Resuming the factory's own stop; a stop of all factories is resumed there.
  const resumable = !!view.own;
  return (
    <>
      {stopped ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm"
        >
          <span className="flex-1">{view.message}</span>
          {resumable && (
            <Button size="sm" onClick={() => setOpen(true)}>
              Resume factory
            </Button>
          )}
        </div>
      ) : (
        <Button variant="destructive" onClick={() => setOpen(true)}>
          Stop factory
        </Button>
      )}
      <StopDialog
        open={open}
        onOpenChange={setOpen}
        resuming={stopped}
        scope="factory"
        onSubmit={(reason) =>
          (stopped ? factoryStop.resume(reason) : factoryStop.stop(reason)).then(
            reload,
          )
        }
      />
    </>
  );
}

/** Over a factory's chats while it is stopped. */
export function FactoryStopBanner({ className = "" }: { className?: string }) {
  const { view } = useFactoryStop();
  if (!view?.stopped) return null;
  return (
    <div
      role="alert"
      className={`rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm ${className}`}
    >
      {view.message}
    </div>
  );
}
