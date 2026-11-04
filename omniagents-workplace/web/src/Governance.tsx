// A factory's data classification, and the services a deployment allows.
import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { toast } from "sonner";
import { factoryProfile, governance, type AllowedService, type GovernanceDecision } from "./api";

const CLASSES: Record<string, string> = {
  published: "Published: public information",
  controlled: "Controlled: internal information",
  confidential: "Confidential: PHI, student records and other personal data",
};

type ProfileFields = {
  classification: string;
  heightened_risk: string;
  purpose: string;
  owner: string;
  sponsor: string;
  data: string;
  affected: string;
  decides: string;
  reason: string;
};

const emptyProfile: ProfileFields = {
  classification: "",
  heightened_risk: "",
  purpose: "",
  owner: "",
  sponsor: "",
  data: "",
  affected: "",
  decides: "",
  reason: "",
};

/** Classification and risk, purpose, owner, sponsor and intended use. Raising a
 * label takes effect at once; lowering one is the governance owner's decision. */
export function FactoryProfileForm({ onSaved }: { onSaved?: () => void }) {
  const [fields, setFields] = useState<ProfileFields>(emptyProfile);
  const [forced, setForced] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const set = (change: Partial<ProfileFields>) => setFields((f) => ({ ...f, ...change }));
  useEffect(() => {
    void factoryProfile.get().then((view) => {
      const p = view.profile;
      setFields({
        ...emptyProfile,
        classification: p.classification ?? "",
        heightened_risk: p.heightened_risk == null ? "" : p.heightened_risk ? "yes" : "no",
        purpose: p.purpose ?? "",
        owner: p.owner ?? "",
        sponsor: p.sponsor ?? "",
        data: p.data ?? "",
        affected: p.affected ?? "",
        decides: p.decides ?? "",
      });
      setForced(view.review_forced);
      setPending(view.pending[0]?.summary ?? null);
    });
  }, []);
  const text = (key: keyof ProfileFields, label: string, max = 2000) => (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      <Input value={fields[key]} onChange={(e) => set({ [key]: e.target.value })} maxLength={max} />
    </label>
  );
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        factoryProfile
          .save({
            ...fields,
            heightened_risk: fields.heightened_risk === "" ? null : fields.heightened_risk === "yes",
          })
          .then((view) => {
            setForced(view.review_forced);
            if (view.pending) {
              setPending(view.pending.summary);
              toast.success("Saved. The lower classification waits for the governance owner.");
            } else {
              toast.success("Saved");
              onSaved?.();
            }
          })
          .catch((e: unknown) => toast.error((e as Error).message));
      }}
    >
      {pending && (
        <p role="status" className="rounded-md border p-2 text-sm">
          Waiting for the governance owner: {pending}
        </p>
      )}
      <label className="block space-y-1">
        <span className="text-sm font-medium">Data classification</span>
        <NativeSelect
          value={fields.classification}
          onChange={(e) => set({ classification: e.target.value })}
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
        <span className="text-sm font-medium">Heightened risk</span>
        <NativeSelect
          value={fields.heightened_risk}
          onChange={(e) => set({ heightened_risk: e.target.value })}
          required
          aria-label="Heightened risk"
        >
          <NativeSelectOption value="">Choose…</NativeSelectOption>
          <NativeSelectOption value="yes">Yes: it may materially affect people&apos;s rights, opportunities, safety or access to services</NativeSelectOption>
          <NativeSelectOption value="no">No: it prepares work, and people decide</NativeSelectOption>
        </NativeSelect>
      </label>
      {text("purpose", "Purpose")}
      {text("owner", "Owner", 200)}
      {text("sponsor", "Sponsor: the clinical or business leader it is built for", 200)}
      {text("data", "Data it touches")}
      {text("affected", "Who is affected")}
      {text("decides", "What the AI does and does not decide")}
      {text("reason", "Reason (needed to lower the classification or risk)")}
      {forced && (
        <p className="text-sm text-muted-foreground">
          Every change to this factory&apos;s definition arrives by a reviewed pull request.
        </p>
      )}
      <Button type="submit" disabled={!fields.classification || !fields.heightened_risk}>
        Save
      </Button>
    </form>
  );
}

function DecisionList({
  decisions,
  owner,
  decide,
}: {
  decisions: GovernanceDecision[];
  owner: boolean;
  decide: (id: string, approve: boolean, reasoning: string) => Promise<unknown>;
}) {
  if (!decisions.length) return <p className="text-sm text-muted-foreground">No decisions yet.</p>;
  return (
    <ul className="divide-y rounded-md border text-sm">
      {[...decisions].reverse().map((d) => (
        <li key={d.id} className="flex flex-wrap items-center gap-2 p-2">
          <span className="flex-1">
            <span className="font-medium">{d.summary}</span>
            <span className="block text-xs text-muted-foreground">
              {d.status === "pending"
                ? `Proposed by ${d.proposed_by ?? "—"}${d.reason ? `: ${d.reason}` : ""}`
                : `${d.status === "declined" ? "Declined" : "Decided"} by ${d.decided_by ?? "—"}${d.reasoning ? `: ${d.reasoning}` : ""}`}
            </span>
          </span>
          {d.status === "pending" && owner && (
            <>
              <Button size="sm" onClick={() => {
                const reasoning = window.prompt("Your reasoning") ?? "";
                void decide(d.id, true, reasoning).catch((e: unknown) => toast.error((e as Error).message));
              }}>Approve</Button>
              <Button size="sm" variant="outline" onClick={() => {
                const reasoning = window.prompt("Why decline?") ?? "";
                void decide(d.id, false, reasoning).catch((e: unknown) => toast.error((e as Error).message));
              }}>Decline</Button>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

/** A factory's decisions and its system card (Factory settings → Governance). */
export function FactoryGovernance() {
  const [view, setView] = useState<{ decisions: GovernanceDecision[]; owner: boolean } | null>(null);
  const [card, setCard] = useState("");
  const load = () => {
    void governance.get().then(setView);
    void governance.card().then((r) => setCard(r.markdown));
  };
  useEffect(load, []);
  if (!view) return null;
  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <h3 className="font-medium">Decisions</h3>
        <DecisionList
          decisions={view.decisions}
          owner={view.owner}
          decide={(id, approve, reasoning) => governance.decide(id, approve, reasoning).then(load)}
        />
      </section>
      <section className="space-y-2">
        <h3 className="font-medium">System card</h3>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border p-2 text-xs">{card}</pre>
        <Button variant="outline" size="sm" onClick={() => void navigator.clipboard.writeText(card).then(() => toast.success("Copied"))}>
          Copy for the AI Inventory
        </Button>
      </section>
    </div>
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

/** An egress allowlist, one host per line (host, *.domain, or either with :port). */
export function AllowlistEditor({
  load,
  save,
  help,
  allowClear = false,
}: {
  load: () => Promise<string[] | null>;
  save: (entries: string[] | null, reason: string) => Promise<{ pending: unknown }>;
  help: string;
  allowClear?: boolean;
}) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  useEffect(() => {
    void load().then((entries) => setText((entries ?? []).join("\n")));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- loaded once, when opened
  const submit = (entries: string[] | null) =>
    save(entries, reason)
      .then((r) =>
        toast.success(r.pending ? "Saved. Widening it waits for the governance owner." : "Saved"),
      )
      .catch((e: unknown) => toast.error((e as Error).message));
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(text.split("\n").map((l) => l.trim()).filter(Boolean));
      }}
    >
      <p className="text-sm text-muted-foreground">{help}</p>
      <textarea
        aria-label="Allowlist"
        className="h-40 w-full rounded-md border p-2 font-mono text-sm"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Input placeholder="Reason (needed to widen it)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="flex gap-2">
        <Button type="submit">Save</Button>
        {allowClear && (
          <Button type="button" variant="outline" onClick={() => void submit(null)}>
            Use the deployment&apos;s list
          </Button>
        )}
      </div>
    </form>
  );
}
