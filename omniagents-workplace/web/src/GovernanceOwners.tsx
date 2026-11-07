import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { governance, type GovernanceState } from "./api";

export function GovernanceOwners({ view, onSaved }: { view: GovernanceState; onSaved: () => Promise<void> }) {
  const [owners, setOwners] = useState(view.owners);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => setOwners(view.owners), [view.owners]);
  if (view.managed_by_installation) return <p className="text-sm text-muted-foreground">Governance owners are managed in the installation's Governance settings. Contact an installation administrator to change them.</p>;
  if (!view.can_manage_owners) return <p className="text-sm text-muted-foreground">Contact a Workplace administrator to change governance owners.</p>;
  return <section className="space-y-2 rounded-md border p-3">
    <h3 className="text-sm font-semibold">Governance owners</h3>
    <p className="text-sm text-muted-foreground">Owners approve governance decisions. When governance review is required, changing models requires both administrator access and governance ownership.</p>
    {!view.owners.length && <p className="text-sm">No governance owner is assigned.</p>}
    <fieldset disabled={busy} className="space-y-2">
      {view.people.filter((person) => !person.disabled || owners.includes(person.id)).map((person) => <label key={person.id} className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={owners.includes(person.id)} onChange={(event) => {
          setSaved(false);
          setOwners(event.target.checked ? [...owners, person.id] : owners.filter((id) => id !== person.id));
        }} />
        {person.name || person.username} (@{person.username}){person.admin ? " · Administrator" : ""}{person.disabled ? " · Disabled" : ""}
      </label>)}
      <Button size="sm" onClick={async () => {
        setBusy(true); setError(""); setSaved(false);
        try {
          await governance.saveOwners(owners);
          await onSaved();
          setSaved(true);
        } catch (error) { setError((error as Error).message); }
        finally { setBusy(false); }
      }}>{busy ? "Saving…" : "Save governance owners"}</Button>
    </fieldset>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {saved && <p role="status" className="text-sm">Governance owners saved.</p>}
  </section>;
}
