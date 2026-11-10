import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { factoryEgress } from "./api";
import { AllowlistEditor } from "./Governance";

type EgressView = Awaited<ReturnType<typeof factoryEgress.get>>;

export function EgressSettings() {
  const [view, setView] = useState<EgressView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const load = () => {
    setError(null);
    return factoryEgress.get().then(setView).catch((failure: unknown) => {
      setError((failure as Error).message);
    });
  };
  useEffect(() => { void load(); }, []);
  const save = async (deployment: boolean, entries: string[] | null, reason: string) => {
    const result = await (deployment ? factoryEgress.saveDeployment : factoryEgress.save)(entries, reason);
    await load();
    setRevision((current) => current + 1);
    return result;
  };
  if (error) return <div role="alert" className="space-y-2"><p>{error}</p><Button variant="outline" onClick={() => void load()}>Retry</Button></div>;
  if (!view) return <p role="status">Loading allowed destinations…</p>;
  return (
    <div className="max-h-[65vh] space-y-6 overflow-y-auto">
      {!view.enabled && <p role="status" className="text-sm text-muted-foreground">The egress proxy is not configured. These lists take effect only when it is enabled.</p>}
      <section className="space-y-2" aria-label="Deployment destinations">
        <h3 className="font-medium">Deployment allowlist</h3>
        <AllowlistEditor
          key={`deployment-${revision}`}
          label="Deployment allowlist"
          allowAdd
          readOnly={view.managed_by_installation}
          help={view.managed_by_installation ? "Managed for all factories from the Factories page → Allowed services → Egress allowlist." : "Checked destinations are allowed through the proxy. Uncheck to remove on Save, or add a hostname below. Network firewall rules are unchanged."}
          load={() => Promise.resolve(view.deployment)}
          save={(entries, reason) => save(true, entries, reason)}
        />
      </section>
      <section className="space-y-2 border-t pt-4" aria-label="Factory restrictions">
        <h3 className="font-medium">Factory allowlist (optional restriction)</h3>
        <AllowlistEditor
          key={`factory-${revision}`}
          label="Factory allowlist"
          allowClear
          choices={view.deployment}
          help="For Confidential factories only: check the approved destinations this factory may use. Other factories use the deployment list. Add new destinations at deployment level, not here."
          load={() => Promise.resolve(view.own)}
          save={(entries, reason) => save(false, entries, reason)}
        />
      </section>
    </div>
  );
}
