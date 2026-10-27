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
  type FactoryModelState,
  type ModelChoice,
  type ProviderKind,
} from "./api";

const fail = (e: unknown) => {
  toast.error((e as Error).message);
};

const PROBE_COPY: Record<string, string> = {
  unauthorized: "The key was refused: check it and try again.",
  "not-found": "Nothing answered there: check the address.",
  network: "Couldn't reach it: check the address and your connection.",
};

export function FactoryModelDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [state, setState] = useState<FactoryModelState | null>(null);
  const load = useCallback(() => factoryModel.get().then(setState, fail), []);
  useEffect(() => {
    if (open) void load();
  }, [open, load]);

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
        {!state ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="space-y-5">
            {state.factory_error && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
                {state.factory_error}. ChatGPT sign-in and the model list need
                the factory running (bin/dev).
              </p>
            )}

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Default</h3>
              <div className="flex gap-2">
                <NativeSelect
                  aria-label="Default model"
                  className="flex-1"
                  value={state.default ?? ""}
                  onChange={(e) =>
                    e.target.value &&
                    void factoryModel
                      .setDefault(e.target.value)
                      .then(load, fail)
                  }
                >
                  <NativeSelectOption value="">
                    The project's (models.yml)
                    {state.runtime?.default ? `: ${state.runtime.default}` : ""}
                  </NativeSelectOption>
                  {choices.map((m) => (
                    <NativeSelectOption key={m.ref} value={m.ref}>
                      {m.label} ({m.ref})
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
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
                <div
                  key={p.name}
                  className="flex items-center gap-3 rounded-md border px-3 py-2 text-sm"
                >
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">
                      {p.name}{" "}
                      <span className="text-xs font-normal text-muted-foreground">
                        {p.type}
                        {p.base_url && ` · ${p.base_url}`}
                        {p.key && ` · key ${p.key}`}
                      </span>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {p.models.map((m) => m.label).join(", ") ||
                        "models from the runtime"}
                    </div>
                  </div>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    title={`Remove ${p.name}`}
                    onClick={() =>
                      void factoryModel
                        .removeProvider(p.name)
                        .then(setState, fail)
                    }
                  >
                    <Trash2 />
                  </Button>
                </div>
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
                    onAdded={setState}
                  />
                </TabsContent>
                <TabsContent value="local" className="pt-3">
                  <AddProvider
                    kinds={["ollama", "openai-compatible"]}
                    onAdded={setState}
                  />
                </TabsContent>
              </Tabs>
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
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
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<ModelChoice[] | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [makeDefault, setMakeDefault] = useState(true);
  const local = kind === "ollama" || kind === "openai-compatible";

  const check = async () => {
    setChecking(true);
    setError(null);
    setOptions(null);
    try {
      const result = await factoryModel.validate({
        kind,
        api_key: apiKey,
        base_url: baseUrl,
      });
      if (!result.ok) {
        setError(
          PROBE_COPY[result.code ?? ""] ?? result.detail ?? "It did not work",
        );
        return;
      }
      const choices = result.choices ?? [];
      setOptions(choices);
      setPicked((choices.find((c) => c.recommended) ?? choices[0])?.id ?? "");
    } catch (e) {
      fail(e);
    } finally {
      setChecking(false);
    }
  };

  const save = () => {
    const model = options?.find((o) => o.id === picked);
    if (!model) return;
    void factoryModel
      .addProvider({
        kind,
        api_key: apiKey,
        base_url: baseUrl,
        model,
        make_default: makeDefault,
      })
      .then((state) => {
        toast.success(`Added ${model.label}`);
        setOptions(null);
        setApiKey("");
        onAdded(state);
      }, fail);
  };

  return (
    <div className="space-y-3 text-sm">
      <div className="flex gap-2">
        <NativeSelect
          value={kind}
          onChange={(e) => {
            const next = e.target.value as ProviderKind;
            setKind(next);
            setOptions(null);
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
            onChange={(e) => setBaseUrl(e.target.value)}
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
            onChange={(e) => {
              setApiKey(e.target.value);
              setOptions(null);
            }}
          />
        </div>
      )}
      <Button
        variant="outline"
        onClick={() => void check()}
        disabled={
          checking || (!local && !apiKey.trim()) || (local && !baseUrl.trim())
        }
      >
        {checking ? "Checking…" : "Check"}
      </Button>
      {error && <p className="text-destructive">{error}</p>}
      {options && (
        <div className="space-y-2">
          {options.length === 0 && (
            <p className="text-muted-foreground">
              It answered, but lists no models.
            </p>
          )}
          {options.map((o) => (
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
          <Button onClick={save} disabled={!picked}>
            Save
          </Button>
        </div>
      )}
    </div>
  );
}
