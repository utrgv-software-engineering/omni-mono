// A factory's data classification, and the services a deployment allows.
import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { toast } from "sonner";
import { factoryProfile, type AllowedService } from "./api";

const CLASSES: Record<string, string> = {
  published: "Published: public information",
  controlled: "Controlled: internal information",
  confidential: "Confidential: PHI, student records and other personal data",
};

/** Classification, purpose and owner (factory administrators). */
export function FactoryProfileForm({ onSaved }: { onSaved?: () => void }) {
  const [classification, setClassification] = useState("");
  const [purpose, setPurpose] = useState("");
  const [owner, setOwner] = useState("");
  const [forced, setForced] = useState(false);
  useEffect(() => {
    void factoryProfile.get().then((view) => {
      setClassification(view.profile.classification ?? "");
      setPurpose(view.profile.purpose ?? "");
      setOwner(view.profile.owner ?? "");
      setForced(view.review_forced);
    });
  }, []);
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        factoryProfile
          .save({ classification, purpose, owner })
          .then((view) => {
            setForced(view.review_forced);
            toast.success("Saved");
            onSaved?.();
          })
          .catch((e: unknown) => toast.error((e as Error).message));
      }}
    >
      <label className="block space-y-1">
        <span className="text-sm font-medium">Data classification</span>
        <NativeSelect
          value={classification}
          onChange={(e) => setClassification(e.target.value)}
          required
          aria-label="Data classification"
        >
          <NativeSelectOption value="">Choose…</NativeSelectOption>
          {Object.entries(CLASSES).map(([value, label]) => (
            <NativeSelectOption key={value} value={value}>
              {label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <span className="block text-xs text-muted-foreground">
          By the data the factory touches, not its purpose. Controlled and Confidential
          factories use only allowed services, and every change to them is reviewed.
        </span>
      </label>
      <label className="block space-y-1">
        <span className="text-sm font-medium">Purpose</span>
        <Input value={purpose} onChange={(e) => setPurpose(e.target.value)} maxLength={2000} />
      </label>
      <label className="block space-y-1">
        <span className="text-sm font-medium">Owner</span>
        <Input value={owner} onChange={(e) => setOwner(e.target.value)} maxLength={200} />
      </label>
      {forced && (
        <p className="text-sm text-muted-foreground">
          Every change to this factory&apos;s definition arrives by a reviewed pull request.
        </p>
      )}
      <Button type="submit" disabled={!classification}>
        Save
      </Button>
    </form>
  );
}

const blank: AllowedService = { kind: "model", name: "", provider: "", endpoint: "", note: "" };

/** The deployment's allowed services, edited as a list and saved together. */
export function AllowedServicesEditor({
  load,
  save,
  readOnly = false,
}: {
  load: () => Promise<AllowedService[]>;
  save: (services: AllowedService[]) => Promise<AllowedService[]>;
  readOnly?: boolean;
}) {
  const [services, setServices] = useState<AllowedService[]>([]);
  useEffect(() => {
    void load().then(setServices);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- loaded once, when opened
  const update = (index: number, change: Partial<AllowedService>) =>
    setServices((list) => list.map((s, i) => (i === index ? { ...s, ...change } : s)));
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        save(services)
          .then((saved) => {
            setServices(saved);
            toast.success("Saved");
          })
          .catch((e: unknown) => toast.error((e as Error).message));
      }}
    >
      <p className="text-sm text-muted-foreground">
        Models every factory may use, and the integrations and MCP servers Controlled
        and Confidential factories may use. Changes reach a factory when its runtime
        next starts.
      </p>
      {services.map((service, index) => (
        <fieldset key={index} className="grid gap-2 rounded-md border p-3 md:grid-cols-[8rem_1fr_1fr_2fr_auto]" disabled={readOnly}>
          <NativeSelect
            aria-label="Kind"
            value={service.kind}
            onChange={(e) => update(index, { kind: e.target.value as AllowedService["kind"] })}
          >
            <NativeSelectOption value="model">Model</NativeSelectOption>
            <NativeSelectOption value="integration">Integration</NativeSelectOption>
            <NativeSelectOption value="mcp">MCP server</NativeSelectOption>
          </NativeSelect>
          <Input aria-label="Name" placeholder="Name" value={service.name} required onChange={(e) => update(index, { name: e.target.value })} />
          <Input
            aria-label="Provider or type"
            placeholder={service.kind === "mcp" ? "—" : service.kind === "model" ? "azure, openai…" : "slack, github…"}
            value={service.provider}
            disabled={service.kind === "mcp"}
            onChange={(e) => update(index, { provider: e.target.value })}
          />
          <Input
            aria-label="Endpoint"
            placeholder={service.kind === "mcp" ? "URL or command:executable" : "URL (empty: the provider's own)"}
            value={service.endpoint}
            onChange={(e) => update(index, { endpoint: e.target.value })}
          />
          {!readOnly && (
            <Button type="button" variant="ghost" onClick={() => setServices((list) => list.filter((_, i) => i !== index))}>
              Remove
            </Button>
          )}
        </fieldset>
      ))}
      {!readOnly && (
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={() => setServices((list) => [...list, { ...blank }])}>
            Add a service
          </Button>
          <Button type="submit">Save</Button>
        </div>
      )}
    </form>
  );
}
