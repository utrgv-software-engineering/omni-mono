import { factoryUrl } from "./factory-scope";
// One of the factory's runs, as the agents did it: its sessions (its own, and
// those of the agents it called as tools) through the shared chat UI,
// read-only, live while it runs. The workplace proxies the factory's agent
// runtimes (factory_runtime.py), so the page needs no factory token.
import { App } from "@omniagents/chat-ui/App";
import { type ChatHost, ChatHostProvider } from "@omniagents/chat-ui/host";
import { RPCClientProvider } from "@omniagents/chat-ui/rpc-context";
import { UiConfigProvider } from "@omniagents/chat-ui/ui-config";
import { TooltipProvider } from "@omniagents/ui/tooltip";
import { ArrowLeft } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Popover, PopoverContent, PopoverTrigger } from "@omniagents/ui/popover";
import { type FactoryRunCase, factoryRun } from "./api";
import { scopeLabel } from "./definition-api";

const CLIENT_IDENTITY = { name: "omniagents-workplace", version: "workplace" };

const host: ChatHost = {
  notifyError: (title, description) => {
    toast.error(title, { description });
  },
  voiceMode: "hosted",
  audioSettings: () => ({
    inputDeviceId: null,
    outputDeviceId: null,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  }),
};

/** A connection to an agent's runtime on the factory, through the workplace. */
function AgentRuntime({ agent, runId, children }: { agent: string; runId: number; children: React.ReactNode }) {
  const connection = useMemo(
    () => ({ baseUrl: factoryUrl(`${window.location.origin}/workplace/api/factory/runs/${runId}/runtime/agents/${agent}/`) }),
    [agent, runId],
  );
  return (
    <UiConfigProvider connection={connection}>
      <RPCClientProvider identity={CLIENT_IDENTITY}>{children}</RPCClientProvider>
    </UiConfigProvider>
  );
}

export function FactoryRun({
  runId,
  navigate,
}: {
  runId: number;
  navigate: (p: string) => void;
}) {
  const [run, setRun] = useState<FactoryRunCase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  useEffect(() => {
    factoryRun.get(runId).then(
      (r) => {
        setRun(r);
        const requested = new URLSearchParams(window.location.search).get("session");
        setSessionId((current) => current ?? r.sessions.find(s => s.session_id === requested)?.session_id ?? r.sessions[0]?.session_id ?? null);
      },
      (e: unknown) => setError((e as Error).message),
    );
  }, [runId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
        <button
          type="button"
          onClick={() => navigate("/factory")}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> The factory
        </button>
        {run && (
          <>
            <h1 className="font-semibold">
              Run {run.id}: {run.automation}
            </h1>
            <span className="text-xs text-muted-foreground">
              {run.provider} {run.event} ·{" "}
              {new Date(run.started_at * 1000).toLocaleString()}
              {run.version ? ` · factory ${run.version.slice(0, 7)}` : ""}
            </span>
            {run.skills && run.skills.offered.length + run.skills.loaded.length > 0 && <RunSkills skills={run.skills} />}
            {run.pull_requests.map((p) => (
              <button
                key={`${p.repository}#${p.number}`}
                type="button"
                className="text-xs text-sky-700 hover:underline dark:text-sky-300"
                onClick={() => navigate(`/${p.repository}/pull/${p.number}`)}
              >
                {p.repository}#{p.number} ({p.relation})
              </button>
            ))}
          </>
        )}
      </div>
      {error && <p className="p-4 text-sm text-destructive">{error}</p>}
      {run && (
        // Phones: the sessions are a row above the transcript.
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <nav
            className="flex shrink-0 gap-1 overflow-x-auto border-b p-2 text-sm [scrollbar-width:none] md:w-56 md:flex-col md:gap-0 md:space-y-1 md:overflow-x-visible md:overflow-y-auto md:border-r md:border-b-0"
            aria-label="Sessions"
          >
            <div className="hidden px-2 py-1 text-xs text-muted-foreground md:block">Sessions</div>
            {run.sessions.map((s, i) => (
              <button
                key={s.session_id}
                type="button"
                onClick={() => setSessionId(s.session_id)}
                className={`block shrink-0 rounded px-3 py-1.5 text-left md:w-full md:px-2 ${s.session_id === sessionId ? "bg-accent font-medium" : "hover:bg-accent/50"}`}
              >
                <div className="whitespace-nowrap">{s.agent}</div>
                <div className="text-xs whitespace-nowrap text-muted-foreground md:whitespace-normal">
                  {i === 0 ? "the run" : "called as a tool"}
                  {s.opened?.length ? ` · opened ${s.opened.join(", ")}` : ""}
                </div>
              </button>
            ))}
          </nav>
          <div className="min-h-0 min-w-0 flex-1">
            {sessionId && (
              <ChatHostProvider host={host}>
                <TooltipProvider delayDuration={300}>
                  {/* Child sessions live with the run's agent: its runtime reads them. */}
                  <AgentRuntime agent={run.agent} runId={runId}>
                    <App key={sessionId} sessionId={sessionId} readOnly allowQuestionResponses />
                  </AgentRuntime>
                </TooltipProvider>
              </ChatHostProvider>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** The skills a run was offered, which it loaded, and where each came from. */
function RunSkills({ skills }: { skills: NonNullable<FactoryRunCase["skills"]> }) {
  const loaded = new Set(skills.loaded.map((s) => s.name));
  const offered = skills.offered.length ? skills.offered : skills.loaded;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="text-xs text-sky-700 hover:underline dark:text-sky-300">
          Skills · {loaded.size} loaded of {offered.length}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-2" aria-label="Skills">
        <ul className="flex flex-col gap-1 text-sm">
          {offered.map((s) => (
            <li key={s.name} className="flex items-baseline gap-2 px-1">
              <span className={loaded.has(s.name) ? "font-medium" : "text-muted-foreground"}>{s.name}</span>
              <span className="ml-auto text-xs text-muted-foreground">
                {loaded.has(s.name) ? "loaded · " : ""}
                {scopeLabel(s.source, (key) => key)}
              </span>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
