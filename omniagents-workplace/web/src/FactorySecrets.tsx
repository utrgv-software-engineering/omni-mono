import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@omniagents/ui/field";
import { Input } from "@omniagents/ui/input";
import { Separator } from "@omniagents/ui/separator";
import { toast } from "sonner";
import { factorySecrets } from "./api";

export function FactorySecrets() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    factorySecrets.get().then(
      (state) => { if (live) setConfigured(state.configured.includes("SERPAPI_API_KEY")); },
      (e: Error) => { if (live) setError(e.message); },
    );
    return () => { live = false; };
  }, []);
  async function save(value: string | null) {
    setBusy(true);
    setError("");
    try {
      const state = await factorySecrets.save({ SERPAPI_API_KEY: value });
      setConfigured(state.configured.includes("SERPAPI_API_KEY"));
      setKey("");
      toast.success(value === null ? "Search key removed" : "Search key saved");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Separator />
      <form onSubmit={(e) => { e.preventDefault(); void save(key.trim()); }}>
        <FieldGroup>
          <Field data-invalid={!!error}>
            <FieldLabel htmlFor="serpapi-key">SerpAPI key</FieldLabel>
            <Input id="serpapi-key" type="password" autoComplete="new-password"
              value={key} onChange={(e) => setKey(e.target.value)}
              placeholder={configured ? "Saved — enter a replacement" : "Enter API key"}
              aria-invalid={!!error} disabled={busy || configured === null} />
            <FieldDescription>
              Web search for this factory. Stored encrypted; changes apply when the factory restarts.
            </FieldDescription>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          </Field>
          <div className="flex justify-end gap-2">
            {configured && <Button type="button" variant="outline" disabled={busy}
              onClick={() => void save(null)}>Remove key</Button>}
            <Button type="submit" disabled={busy || configured === null || !key.trim()}>Save key</Button>
          </div>
        </FieldGroup>
      </form>
    </>
  );
}
