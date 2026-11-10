import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { factoryBase } from "./factory-scope";

type Deployment = { enabled: boolean; ready: boolean; url: string; error?: string | null };

// The rail opens native Coolify. Only startup and failures stay in Workplace.
export function Coolify() {
  const id = factoryBase().split("/").pop();
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const response = await fetch("/workplace/api/factories", { signal: controller.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Unable to open Coolify");
        const factory = data.factories.find((f: { id: string }) => f.id === id);
        if (!factory || factory.role !== "admin") throw new Error("Factory administrator access required");
        const next = factory.deployment as Deployment | null;
        if (!next) throw new Error("Coolify is unavailable for this factory");
        if (controller.signal.aborted) return;
        setDeployment(next);
        setError("");
        if (next.ready) {
          window.location.replace(`${next.url}/__workplace/start`);
          return;
        }
        timer = setTimeout(() => void refresh(), 3000);
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id]);
  async function start() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`/workplace/api/factories/${id}/coolify/start`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to start Coolify");
      setDeployment(data);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="workplace-page flex flex-col items-start gap-3">
    {error ? <p role="alert">{error}</p> : deployment?.error ? <p role="alert">{deployment.error}</p> :
      <p>{!deployment || deployment.ready ? "Opening Coolify…" : deployment.enabled ? "Starting Coolify…" : "Coolify is stopped."}</p>}
    {deployment && (!deployment.enabled || deployment.error) && <Button disabled={busy} onClick={() => void start()}>{busy ? "Starting…" : "Start Coolify"}</Button>}
  </div>;
}
