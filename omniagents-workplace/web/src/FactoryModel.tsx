// The factory's model, as omni-desktop's AI settings: sign in with ChatGPT
// (a device code: approve it on any device), use an API key or a local
// server, and choose the default. The workplace writes the factory's
// models.json; the ChatGPT sign-in is the factory runtime's own.
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@omniagents/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@omniagents/ui/dialog";
import { Input } from "@omniagents/ui/input";
import { Label } from "@omniagents/ui/label";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@omniagents/ui/tabs";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  factoryModel,
  governance,
  type GovernanceState,
  type FactoryModelState,
  type ModelChoice,
  type ProviderKind,
  type FactoryModelEdit,
} from "./api";
import { GovernanceOwners } from "./GovernanceOwners";

const fail = (e: unknown) => {
  toast.error((e as Error).message);
};

const PROBE_COPY: Record<string, string> = {
  unauthorized: "The key was refused: check it and try again.",
  "not-found": "Nothing answered there: check the address.",
  network: "Workplace couldn't reach the model listing. Check the address and the factory's allowed destinations.",
};

export function FactoryModelDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [state, setState] = useState<FactoryModelState | null>(null);
  const [permissions, setPermissions] = useState<GovernanceState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [defaultPending, setDefaultPending] = useState(false);
  const [defaultError, setDefaultError] = useState("");
  const generation = useRef(0);
  const load = useCallback(async () => {
    const current = ++generation.current;
    setPermissions(null);
    setLoadError("");
    setDefaultPending(false);
    setDefaultError("");
    try {
      const [model, access] = await Promise.all([factoryModel.get(), governance.get()]);
      if (current !== generation.current) return;
      setState(model);
      setPermissions(access);
    } catch (error) {
      if (current === generation.current) setLoadError((error as Error).message);
    }
  }, []);
  useEffect(() => {
    if (open) void load();
    return () => { generation.current++; };
  }, [open, load]);

  const applySaved = (saved: FactoryModelState) => setState((current) => current ? {
    ...current, path: saved.path, providers: saved.providers, default: saved.default, voice_default: saved.voice_default,
  } : saved);
  const readOnly = !permissions || (permissions.gated && !permissions.owner);
  const changeDefault = async (ref: string) => {
    if (readOnly || defaultPending) return;
    setDefaultPending(true);
    setDefaultError("");
    const current = generation.current;
    try {
      const saved = await factoryModel.setDefault(ref);
      if (current === generation.current) applySaved(saved);
    } catch (error) {
      if (current === generation.current) setDefaultError((error as Error).message);
    } finally {
      if (current === generation.current) setDefaultPending(false);
    }
  };

  const choices = state
    ? Array.from(new Map([
        ...state.providers.flatMap((p) => p.models.filter((m) => !m.realtime)),
        ...(state.runtime?.models.filter((m) => !m.realtime && m.available) ?? []),
      ].map((m) => [m.ref, m])).values())
    : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>The factory's model</DialogTitle>
          <DialogDescription>
            What the factory's agents run on. Saved in the factory's{" "}
            <code>{state?.path ?? "models.json"}</code> (over its project's
            models.yml); a run picks it up as it starts.
          </DialogDescription>
        </DialogHeader>
        {loadError ? (
          <div role="alert" className="space-y-2 text-sm">
            <p>Could not load model settings: {loadError}</p>
            <Button variant="outline" onClick={() => void load()}>Retry</Button>
          </div>
        ) : !state || !permissions ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="space-y-5">
            {permissions.gated && !permissions.owner && <div className="space-y-3">
              <p role="status" className="rounded-md border p-3 text-sm">Only governance owners can change models. These settings are read-only until you are assigned as an owner.</p>
              <GovernanceOwners view={permissions} onSaved={load} />
            </div>}
            {state.factory_error && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
                {state.factory_error}. ChatGPT sign-in and the model list need
                the factory running (bin/dev).
              </p>
            )}

            <fieldset disabled={permissions.gated && !permissions.owner} className="space-y-5">
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Default</h3>
              <div className="flex gap-2">
                <NativeSelect
                  aria-label="Default model"
                  className="flex-1"
                  value={state.default ?? ""}
                  disabled={defaultPending}
                  onChange={(e) => void changeDefault(e.target.value)}
                >
                  <NativeSelectOption value="">
                    The project's (models.yml)
                    {state.runtime?.default ? `: ${state.runtime.default}` : ""}
                  </NativeSelectOption>
                  {state.default && !choices.some((m) => m.ref === state.default) && <NativeSelectOption value={state.default}>{state.default} (saved reference)</NativeSelectOption>}
                  {choices.map((m) => (
                    <NativeSelectOption key={m.ref} value={m.ref}>
                      {m.label} ({m.ref})
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
              {defaultPending && <p role="status" className="text-xs text-muted-foreground">Saving default…</p>}
              {defaultError && <p role="alert" className="text-sm text-destructive">{defaultError}</p>}
              {state.runtime && (
                <ul className="space-y-0.5 text-xs text-muted-foreground">
                  {state.runtime.models
                    .filter((m) => !m.realtime)
                    .map((m) => (
                      <li key={m.ref}>
                        {m.available ? "●" : "○"} {m.label}{" "}
                        <span className="opacity-70">({m.ref})</span>
                        {m.default && " · default"}
                        {!m.available && m.why && ` · ${m.why}`}
                      </li>
                    ))}
                </ul>
              )}
            </section>

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Providers</h3>
              {state.providers.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  None configured here: the factory uses its models.yml.
                </p>
              )}
              {state.providers.map((p) => (
                <ProviderCard key={p.name} provider={p} state={state} readOnly={readOnly} onSaved={applySaved} />
              ))}
            </section>

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Add</h3>
              <Tabs defaultValue="chatgpt">
                <TabsList>
                  <TabsTrigger value="chatgpt">
                    Sign in with ChatGPT
                  </TabsTrigger>
                  <TabsTrigger value="key">API key</TabsTrigger>
                  <TabsTrigger value="local">Local or compatible</TabsTrigger>
                </TabsList>
                <TabsContent value="chatgpt" className="pt-3">
                  <ChatGptSignIn state={state} onChanged={() => void load()} />
                </TabsContent>
                <TabsContent value="key" className="pt-3">
                  <AddProvider
                    kinds={["openai", "anthropic"]}
                    onAdded={applySaved}
                  />
                </TabsContent>
                <TabsContent value="local" className="pt-3">
                  <AddProvider
                    kinds={["ollama", "openai-compatible"]}
                    onAdded={applySaved}
                  />
                </TabsContent>
              </Tabs>
            </section>
            </fieldset>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

type SavedProvider = FactoryModelState["providers"][number];
type SavedModel = SavedProvider["models"][number];

function ProviderCard({ provider, state, readOnly, onSaved }: {
  provider: SavedProvider;
  state: FactoryModelState;
  readOnly: boolean;
  onSaved: (state: FactoryModelState) => void;
}) {
  const [editingProvider, setEditingProvider] = useState(false);
  const [editingModel, setEditingModel] = useState<SavedModel | "new" | null>(null);
  const [removing, setRemoving] = useState<SavedModel | "provider" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (readOnly) { setEditingProvider(false); setEditingModel(null); setRemoving(null); }
  }, [readOnly]);
  const matchesRef = (model: SavedModel, ref: string | null) => model.ref === ref || (model.id ?? model.ref.slice(provider.name.length + 1)) === ref;
  const protectedRef = (model: SavedModel) => matchesRef(model, state.default) || matchesRef(model, state.voice_default);
  const protectedProvider = [state.default, state.voice_default].some((ref) => ref?.startsWith(`${provider.name}/`) || provider.models.some((model) => matchesRef(model, ref)));
  const oauth = provider.type === "openai-oauth";
  const start = (action: () => void) => {
    if (readOnly || busy) return;
    setError(""); setEditingProvider(false); setEditingModel(null); setRemoving(null); action();
  };
  const save = async (action: () => Promise<FactoryModelState>) => {
    if (readOnly || busy) return;
    setBusy(true); setError("");
    try {
      const saved = await action();
      if (!alive.current) return;
      onSaved(saved);
      setEditingProvider(false); setEditingModel(null); setRemoving(null);
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return <section aria-label={`Provider ${provider.name}`} className="rounded-md border text-sm">
    <div className="flex flex-wrap items-center gap-2 px-3 py-3">
      <div className="min-w-0 flex-1">
        <h4 className="font-semibold">{provider.name}</h4>
        <p className="break-all text-xs text-muted-foreground">{provider.type}{provider.base_url && ` · ${provider.base_url}`}</p>
        {provider.key && <p className="text-xs text-muted-foreground">API key configured</p>}
      </div>
      {!oauth && <Button size="sm" variant="outline" aria-label={`Edit provider ${provider.name}`} disabled={readOnly || busy} onClick={() => start(() => setEditingProvider(true))}>Edit provider</Button>}
      <Button size="icon-sm" variant="ghost" aria-label={`Remove provider ${provider.name}`} title={protectedProvider ? "Choose another default before removing this provider" : `Remove ${provider.name}`} disabled={readOnly || busy || protectedProvider} onClick={() => start(() => setRemoving("provider"))}><Trash2 /></Button>
    </div>
    {editingProvider && <ProviderEdit provider={provider} busy={busy} onCancel={() => start(() => {})} onSave={(patch) => void save(() => factoryModel.updateProvider(provider.name, patch))} />}
    {oauth && <p className="px-3 pb-3 text-xs text-muted-foreground">Credentials are managed by ChatGPT sign-in below. Discovered models remain available in the default selector.</p>}
    <div className="space-y-2 border-t px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <h5 className="text-xs font-semibold text-muted-foreground">Models</h5>
        <Button size="sm" variant="ghost" aria-label={`Add model to ${provider.name}`} disabled={readOnly || busy} onClick={() => start(() => setEditingModel("new"))}>Add model</Button>
      </div>
      {provider.models.length === 0 && <p className="text-xs text-muted-foreground">No saved models. Add a model ID, or use the runtime's discovered models.</p>}
      <ul className="space-y-2">
        {provider.models.map((model) => <li key={model.ref} className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1">
            <div>{model.label}<span className="ml-2 text-xs text-muted-foreground">{matchesRef(model, state.default) ? "Default" : matchesRef(model, state.voice_default) ? "Voice default" : model.realtime ? "Voice" : ""}</span></div>
            <div className="break-all font-mono text-xs text-muted-foreground">{model.ref}</div>
          </div>
          <Button size="sm" variant="ghost" aria-label={`Edit model ${model.ref}`} disabled={readOnly || busy} onClick={() => start(() => setEditingModel(model))}>Edit</Button>
          <Button size="sm" variant="ghost" aria-label={`Remove model ${model.ref}`} title={protectedRef(model) ? "Choose another default before removing this model" : `Remove ${model.label}`} disabled={readOnly || busy || protectedRef(model)} onClick={() => start(() => setRemoving(model))}>Remove</Button>
        </li>)}
      </ul>
      {editingModel && <ModelEdit key={editingModel === "new" ? "new" : editingModel.ref} provider={provider} model={editingModel === "new" ? null : editingModel} busy={busy} onCancel={() => start(() => {})} onSave={(id, patch) => void save(() => editingModel === "new" ? factoryModel.addModel(provider.name, { id, ...patch }) : factoryModel.updateModel(provider.name, id, patch))} />}
    </div>
    {removing && <div className="space-y-2 border-t px-3 py-3">
      <p>Remove {removing === "provider" ? `provider ${provider.name} and its saved models` : removing.label}?</p>
      <p className="text-xs text-muted-foreground">Removal is saved only after the factory confirms no agent references it. Project models.yml settings are not changed.</p>
      <div className="flex gap-2">
        <Button size="sm" variant="destructive" disabled={busy} onClick={() => void save(() => removing === "provider" ? factoryModel.removeProvider(provider.name) : factoryModel.removeModel(provider.name, removing.id ?? removing.ref.slice(provider.name.length + 1)))}>{busy ? "Removing…" : "Confirm removal"}</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => start(() => {})}>Cancel</Button>
      </div>
    </div>}
    {error && <p role="alert" className="px-3 pb-3 text-destructive">{error}</p>}
  </section>;
}

function ProviderEdit({ provider, busy, onCancel, onSave }: {
  provider: SavedProvider;
  busy: boolean;
  onCancel: () => void;
  onSave: (patch: { base_url?: string; api_key?: string }) => void;
}) {
  const [address, setAddress] = useState(provider.base_url ?? "");
  const [key, setKey] = useState("");
  const compatible = provider.type === "openai-compatible";
  const addressField = <label className="block space-y-1">
    <span>Address{!compatible && " (optional override)"}</span>
    <Input aria-label="Provider address" value={address} onChange={(e) => setAddress(e.target.value)} disabled={busy} />
  </label>;
  return <form className="space-y-3 border-t bg-muted/20 px-3 py-3" onSubmit={(e) => {
    e.preventDefault();
    if (busy || (compatible && !address.trim())) return;
    const patch: { base_url?: string; api_key?: string } = {};
    if (address !== (provider.base_url ?? "")) patch.base_url = address.trim();
    if (key.trim()) patch.api_key = key.trim();
    onSave(patch);
  }}>
    <p className="text-xs text-muted-foreground">Provider ID and type stay fixed so existing model references keep working.</p>
    {compatible && addressField}
    <label className="block space-y-1">
      <span>Replace API key</span>
      <Input aria-label="Replacement API key" type="password" autoComplete="new-password" placeholder="Leave blank to keep the current key" value={key} onChange={(e) => setKey(e.target.value)} disabled={busy} />
      <span className="block text-xs text-muted-foreground">The saved key is never shown. Enter a new key only to rotate it.</span>
    </label>
    {!compatible && <details><summary className="cursor-pointer text-xs">Advanced</summary><div className="pt-2">{addressField}</div></details>}
    <div className="flex gap-2"><Button size="sm" type="submit" disabled={busy || (compatible && !address.trim())}>{busy ? "Saving…" : "Save provider"}</Button><Button size="sm" type="button" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button></div>
  </form>;
}

function ModelEdit({ provider, model, busy, onCancel, onSave }: {
  provider: SavedProvider;
  model: SavedModel | null;
  busy: boolean;
  onCancel: () => void;
  onSave: (id: string, patch: FactoryModelEdit) => void;
}) {
  const existingId = model?.id ?? model?.ref.slice(provider.name.length + 1) ?? "";
  const [id, setId] = useState(existingId);
  const [label, setLabel] = useState(model?.label ?? "");
  const [target, setTarget] = useState(model?.model ?? "");
  const [maxInput, setMaxInput] = useState(model?.maxInput?.toString() ?? "");
  const [maxOutput, setMaxOutput] = useState(model?.maxOutput?.toString() ?? "");
  const validLimit = (value: string) => !value || (/^[0-9]+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0);
  const valid = !!id.trim() && (!model || !!label.trim()) && validLimit(maxInput) && validLimit(maxOutput);
  return <form className="space-y-3 rounded-md border bg-muted/20 p-3" onSubmit={(e) => {
    e.preventDefault();
    if (busy || !valid) return;
    const patch: FactoryModelEdit = {};
    if (!model || label !== model.label) patch.label = label.trim() || id.trim();
    if (target.trim() && target !== (model?.model ?? "")) patch.model = target.trim();
    if (maxInput && maxInput !== (model?.maxInput?.toString() ?? "")) patch.maxInput = Number(maxInput);
    if (maxOutput && maxOutput !== (model?.maxOutput?.toString() ?? "")) patch.maxOutput = Number(maxOutput);
    onSave(id.trim(), patch);
  }}>
    <h5 className="font-medium">{model ? "Edit model" : `Add model to ${provider.name}`}</h5>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="space-y-1"><span>Model ID</span><Input aria-label="Model ID" value={id} disabled={busy || !!model} onChange={(e) => setId(e.target.value)} /></label>
      <label className="space-y-1"><span>Display label{!model && " (optional)"}</span><Input aria-label="Model label" value={label} disabled={busy} onChange={(e) => setLabel(e.target.value)} /></label>
    </div>
    <p className="text-xs text-muted-foreground">{model ? "The model ID stays fixed to preserve default and agent references." : "Enter the provider's model or deployment ID. No discovery or connectivity check is required."}</p>
    <details>
      <summary className="cursor-pointer text-xs">Advanced</summary>
      <div className="space-y-3 pt-2">
        <label className="block space-y-1"><span>Model or deployment name</span><Input aria-label="Model target" placeholder={id || "Uses the model ID"} value={target} disabled={busy} onChange={(e) => setTarget(e.target.value)} /></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1"><span>Input token limit</span><Input aria-label="Input token limit" type="number" min="1" step="1" value={maxInput} disabled={busy} onChange={(e) => setMaxInput(e.target.value)} /></label>
          <label className="space-y-1"><span>Output token limit</span><Input aria-label="Output token limit" type="number" min="1" step="1" value={maxOutput} disabled={busy} onChange={(e) => setMaxOutput(e.target.value)} /></label>
        </div>
        <p className="text-xs text-muted-foreground">Blank fields keep existing settings; new models use their ID and the factory's token limits. Other model options are unchanged.</p>
      </div>
    </details>
    <div className="flex gap-2"><Button size="sm" type="submit" disabled={busy || !valid}>{busy ? "Saving…" : "Save model"}</Button><Button size="sm" type="button" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button></div>
  </form>;
}

function ChatGptSignIn({
  state,
  onChanged,
}: {
  state: FactoryModelState;
  onChanged: () => void;
}) {
  const [code, setCode] = useState<{ user: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const stopped = useRef(false);
  useEffect(
    () => () => {
      stopped.current = true;
    },
    [],
  );
  const signedIn = state.chatgpt?.state === "signed_in";

  const signIn = async () => {
    setBusy(true);
    stopped.current = false;
    try {
      const started = await factoryModel.chatgptStart();
      if (started.state !== "completed") {
        if (
          !started.login_id ||
          !started.user_code ||
          !started.verification_url
        )
          throw new Error("The factory returned an incomplete sign-in");
        setCode({ user: started.user_code, url: started.verification_url });
        const every = Math.max(started.interval ?? 5, 1) * 1000;
        const deadline = Date.now() + 15 * 60_000;
        for (;;) {
          if (stopped.current) return;
          if (Date.now() > deadline)
            throw new Error("The sign-in timed out: start again");
          await new Promise((r) => setTimeout(r, every));
          const done = await factoryModel.chatgptComplete(started.login_id);
          if (done.state === "completed") break;
        }
      }
      toast.success("Signed in with ChatGPT");
      setCode(null);
      onChanged();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  if (signedIn)
    return (
      <div className="flex items-center gap-3 text-sm">
        Signed in{state.chatgpt?.email ? ` as ${state.chatgpt.email}` : ""}
        {state.chatgpt?.plan ? ` (${state.chatgpt.plan})` : ""}.
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          onClick={() =>
            void factoryModel.chatgptLogout().then(onChanged, fail)
          }
        >
          Sign out
        </Button>
      </div>
    );
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted-foreground">
        The factory runs on your ChatGPT plan: no API key. You approve the
        sign-in on any device.
      </p>
      {code ? (
        <div className="space-y-2 rounded-md border p-3">
          <div>
            Open{" "}
            <a
              href={code.url}
              target="_blank"
              rel="noreferrer"
              className="text-sky-600 underline"
            >
              {code.url}
            </a>{" "}
            and enter:
          </div>
          <div className="font-mono text-2xl font-semibold tracking-widest">
            {code.user}
          </div>
          <div className="text-xs text-muted-foreground">
            Waiting for you to approve it…
          </div>
        </div>
      ) : (
        <Button
          onClick={() => void signIn()}
          disabled={busy || !!state.factory_error}
        >
          Sign in with ChatGPT
        </Button>
      )}
    </div>
  );
}

const KIND_LABEL: Record<ProviderKind, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
  "openai-compatible": "OpenAI-compatible server",
};

function AddProvider({
  kinds,
  onAdded,
}: {
  kinds: ProviderKind[];
  onAdded: (state: FactoryModelState) => void;
}) {
  const [kind, setKind] = useState<ProviderKind>(kinds[0]);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(
    kinds[0] === "ollama" ? "http://localhost:11434" : "",
  );
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const probeVersion = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<ModelChoice[] | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [makeDefault, setMakeDefault] = useState(true);
  const local = kind === "ollama" || kind === "openai-compatible";
  const invalidate = () => {
    probeVersion.current += 1;
    setChecking(false);
    setOptions(null);
    setError(null);
  };

  const check = async () => {
    const version = ++probeVersion.current;
    setChecking(true);
    setError(null);
    setOptions(null);
    try {
      const result = await factoryModel.validate({
        kind,
        api_key: apiKey,
        base_url: baseUrl,
      });
      if (version !== probeVersion.current) return;
      if (!result.ok) {
        setError(
          PROBE_COPY[result.code ?? ""] ?? result.detail ?? "It did not work",
        );
        return;
      }
      const choices = result.choices ?? [];
      setOptions(choices);
      setPicked((current) => current || (choices.find((c) => c.recommended) ?? choices[0])?.id || "");
    } catch (e) {
      if (version === probeVersion.current) setError((e as Error).message);
    } finally {
      if (version === probeVersion.current) setChecking(false);
    }
  };

  const save = async () => {
    const modelId = picked.trim();
    const model = options?.find((o) => o.id === modelId) ?? (modelId ? {
      id: modelId, label: modelId, blurb: "", maxInput: 0, maxOutput: 0, verified: false,
    } : null);
    if (!model) return;
    setSaving(true);
    await factoryModel
      .addProvider({
        kind,
        api_key: apiKey,
        base_url: baseUrl,
        model,
        make_default: makeDefault,
      })
      .then((state) => {
        toast.success(`Added ${model.label}`);
        invalidate();
        setPicked("");
        setApiKey("");
        onAdded(state);
      }, fail).finally(() => setSaving(false));
  };

  return (
    <div className="space-y-3 text-sm">
      <div className="flex gap-2">
        <NativeSelect
          value={kind}
          disabled={saving}
          onChange={(e) => {
            const next = e.target.value as ProviderKind;
            setKind(next);
            invalidate();
            setPicked("");
            setBaseUrl(next === "ollama" ? "http://localhost:11434" : "");
          }}
        >
          {kinds.map((k) => (
            <NativeSelectOption key={k} value={k}>
              {KIND_LABEL[k]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        {local && (
          <Input
            aria-label="Address"
            placeholder={
              kind === "ollama" ? "http://localhost:11434" : "https://host/v1"
            }
            value={baseUrl}
            disabled={saving}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              invalidate();
              setPicked("");
            }}
          />
        )}
      </div>
      {kind !== "ollama" && (
        <div className="space-y-1">
          <Label htmlFor="model-key">
            {local ? "API key (if it takes one)" : "API key"}
          </Label>
          <Input
            id="model-key"
            type="password"
            autoComplete="off"
            placeholder={kind === "anthropic" ? "sk-ant-…" : "sk-…"}
            value={apiKey}
            disabled={saving}
            onChange={(e) => {
              setApiKey(e.target.value);
              invalidate();
            }}
          />
        </div>
      )}
      <Button
        variant="outline"
        onClick={() => void check()}
        disabled={
          checking || saving || (!local && !apiKey.trim()) || (local && !baseUrl.trim())
        }
      >
        {checking ? "Checking…" : "Check"}
      </Button>
      {error && <p className="text-destructive">{error}</p>}
        <label className="block space-y-1">
          <span>Model or deployment name</span>
          <Input
            aria-label="Model or deployment name"
            value={picked}
            disabled={saving}
            onChange={(event) => setPicked(event.target.value)}
          />
          <span className="block text-xs text-muted-foreground">
             Enter a model ID (for Azure OpenAI, your deployment name). Check optionally lists models;
             you can save without it. Saving does not verify connectivity or credentials.
          </span>
        </label>
        <div className="space-y-2">
          {options?.length === 0 && (
            <p className="text-muted-foreground">
              It answered, but lists no models.
            </p>
          )}
          {options?.map((o) => (
            <label key={o.id} className="flex items-start gap-2">
              <input
                type="radio"
                name="model-choice"
                checked={picked === o.id}
                onChange={() => setPicked(o.id)}
                className="mt-1"
              />
              <span>
                <span className="font-medium">{o.label}</span>
                {o.recommended && (
                  <span className="ml-1 text-xs text-emerald-600">
                    recommended
                  </span>
                )}
                {o.blurb && (
                  <span className="block text-xs text-muted-foreground">
                    {o.blurb}
                  </span>
                )}
              </span>
            </label>
          ))}
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={makeDefault}
              onChange={(e) => setMakeDefault(e.target.checked)}
            />
            Make it the factory's default
          </label>
          <Button onClick={() => void save()} disabled={saving || !picked.trim() || (local && !baseUrl.trim()) || (!local && !apiKey.trim())}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
    </div>
  );
}
