import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@omniagents/ui/button";
import { authMessage, initializeTeams } from "./teams";

export function TeamsGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const started = useRef(false);
  const start = async (interactive: boolean) => {
    setBusy(true);
    setError("");
    try {
      await initializeTeams(interactive);
      setReady(true);
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!started.current) {
      started.current = true;
      void start(false);
    }
    const failed = (event: Event) => {
      setReady(false);
      setError((event as CustomEvent<string>).detail);
    };
    window.addEventListener("workplace-teams-auth-error", failed);
    return () => window.removeEventListener("workplace-teams-auth-error", failed);
  }, []);
  if (ready) return children;
  return <main className="flex min-h-dvh items-center justify-center bg-background p-6 text-foreground">
    <section className="w-full max-w-sm space-y-4 rounded-xl border p-6" aria-label="Workplace Microsoft sign-in">
      <h1 className="text-lg font-semibold">Workplace in Teams</h1>
      {error ? <p role="alert" className="text-sm text-muted-foreground">{error}</p> : <p role="status" className="text-sm text-muted-foreground">Signing in with your Teams account…</p>}
      {error && <Button disabled={busy} onClick={() => void start(true)}>{busy ? "Signing in…" : "Continue with Microsoft"}</Button>}
      <a className="block text-sm underline underline-offset-4" href="/" target="_blank" rel="noreferrer">Open Workplace in browser</a>
    </section>
  </main>;
}
