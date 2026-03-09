import React, { useCallback, useEffect, useState } from "react";
import { Box, Text, useInput, useStdout, type Key } from "ink";
import { api } from "../rpc.js";
import type { NetworkConfig } from "../rpc.js";
import { Panel } from "../components/Panel.js";
import { StatusBar } from "../components/StatusBar.js";
import { HelpOverlay } from "../components/HelpOverlay.js";
import { ConfirmDialog } from "../components/ConfirmDialog.js";
import { TextInput } from "../components/TextInput.js";
import type { Hint } from "../components/KeyHint.js";
import { useTheme, useThemeControl } from "../ThemeContext.js";
import { themeNames, type ThemeName } from "../theme.js";

type Props = { onQuit: () => void };

type SettingsData = {
  config_dir: string;
  projects_count: number;
  tickets_count: number;
  tasks_count: number;
};

type ModelEntry = {
  name: string;
  label: string;
  provider: string;
  provider_name: string;
  model: string | null;
  is_default: boolean;
  is_voice_default: boolean;
  is_user_defined: boolean;
};

type ProviderEntry = {
  name: string;
  type: string;
  base_url: string | null;
  has_api_key: boolean;
  model_count: number;
  is_user_defined: boolean;
};

type McpServer = {
  type?: string;
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
};

type Section = "overview" | "display" | "models" | "providers" | "network" | "env" | "mcp";

type AddingModel = {
  step: "provider_name" | "provider_type" | "model_name" | "model_id";
  provider_name: string;
  provider_type: string;
  model_name: string;
};

type AddingProvider = {
  step: "name" | "type" | "api_key" | "base_url";
  name: string;
  type: string;
  api_key: string;
};

type AddingEnvVar = { step: "key" | "value"; key: string };
type AddingMcp = { step: "name" | "type" | "command" | "args" | "url"; name: string; type: string; command: string };

const SECTIONS: { id: Section; label: string; key: string }[] = [
  { id: "overview", label: "Overview", key: "o" },
  { id: "display", label: "Display", key: "i" },
  { id: "models", label: "Models", key: "m" },
  { id: "providers", label: "Providers", key: "p" },
  { id: "mcp", label: "MCP Servers", key: "M" },
  { id: "network", label: "Network", key: "n" },
  { id: "env", label: "Environment", key: "e" },
];

const HELP_HINTS: Hint[] = [
  { key: "o/i/m/p/M/n/e", label: "switch section" },
  { key: "j/k", label: "scroll" },
  { key: "a", label: "add item" },
  { key: "d", label: "delete item" },
  { key: "D", label: "set default model" },
  { key: "T", label: "test model" },
  { key: "t", label: "toggle (network)" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "q", label: "quit" },
];

function clamp(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(value, max - 1));
}

export function Settings({ onQuit }: Props) {
  const t = useTheme();
  const { themeName, setThemeName } = useThemeControl();
  const [data, setData] = useState<SettingsData | null>(null);
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [networkConfig, setNetworkConfig] = useState<NetworkConfig>({});
  const [envVars, setEnvVars] = useState<Record<string, string>>({});
  const [mcpServers, setMcpServers] = useState<Record<string, McpServer>>({});
  const [section, setSection] = useState<Section>("overview");
  const [scrollIndex, setScrollIndex] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmAction, setConfirmAction] = useState<{ message: string; action: () => void } | null>(null);
  const [addingModel, setAddingModel] = useState<AddingModel | null>(null);
  const [addingProvider, setAddingProvider] = useState<AddingProvider | null>(null);
  const [addingEnvVar, setAddingEnvVar] = useState<AddingEnvVar | null>(null);
  const [addingMcp, setAddingMcp] = useState<AddingMcp | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [testing, setTesting] = useState(false);
  const { stdout } = useStdout();
  const visibleRows = (stdout?.rows ?? 24) - 10;

  const isInputActive = addingModel !== null || addingProvider !== null || addingEnvVar !== null || addingMcp !== null;

  const refresh = useCallback(async () => {
    try {
      const [snapshot, modelList, providerList] = await Promise.all([
        api.snapshot(),
        api.listModels().catch(() => []),
        api.listProviders().catch(() => []),
      ]);
      const s = snapshot as Record<string, unknown>;
      setData({
        config_dir: (s.config_dir as string) ?? "~/.config/omni-code",
        projects_count: ((s.projects as unknown[]) ?? []).length,
        tickets_count: ((s.tickets as unknown[]) ?? []).length,
        tasks_count: ((s.tasks as unknown[]) ?? []).length,
      });
      setModels(modelList as ModelEntry[]);
      setProviders(providerList as ProviderEntry[]);
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  const refreshNetwork = useCallback(async () => {
    try { setNetworkConfig(await api.getNetworkConfig()); } catch {}
  }, []);
  const refreshEnv = useCallback(async () => {
    try { setEnvVars(await api.getEnv()); } catch {}
  }, []);
  const refreshMcp = useCallback(async () => {
    try { const c = await api.mcpList(); setMcpServers(c.mcpServers ?? {}); } catch {}
  }, []);

  useEffect(() => { refresh(); refreshNetwork(); refreshEnv(); refreshMcp(); }, [refresh, refreshNetwork, refreshEnv, refreshMcp]);

  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  const mcpNames = Object.keys(mcpServers);
  const currentListLen =
    section === "display" ? themeNames.length :
    section === "models" ? models.length :
    section === "providers" ? providers.length :
    section === "env" ? Object.keys(envVars).length :
    section === "mcp" ? mcpNames.length :
    0;

  useInput((input: string, key: Key) => {
    if (showHelp || confirmAction || isInputActive || testing) return;
    if (input === "?") { setShowHelp(true); return; }
    if (input === "q" || key.escape) { onQuit(); return; }
    if (input === "r") { refresh(); refreshNetwork(); refreshEnv(); refreshMcp(); setStatus("Refreshed"); return; }

    // Section switching
    const sec = SECTIONS.find((s) => s.key === input);
    if (sec) { setSection(sec.id); setScrollIndex(0); return; }

    // Scrolling
    if ((key.upArrow || input === "k") && currentListLen > 0) { setScrollIndex((v) => clamp(v - 1, currentListLen)); return; }
    if ((key.downArrow || input === "j") && currentListLen > 0) { setScrollIndex((v) => clamp(v + 1, currentListLen)); return; }

    // Select theme
    if (key.return && section === "display") {
      const selected = themeNames[scrollIndex];
      if (selected) {
        setThemeName(selected);
        setStatus(`Theme → ${selected}`);
      }
      return;
    }

    // Add
    if (input === "a") {
      if (section === "models") { setAddingModel({ step: "provider_name", provider_name: "", provider_type: "", model_name: "" }); setInputValue(""); return; }
      if (section === "providers") { setAddingProvider({ step: "name", name: "", type: "", api_key: "" }); setInputValue(""); return; }
      if (section === "env") { setAddingEnvVar({ step: "key", key: "" }); setInputValue(""); return; }
      if (section === "mcp") { setAddingMcp({ step: "name", name: "", type: "stdio", command: "" }); setInputValue(""); return; }
    }

    // Delete
    if (input === "d") {
      if (section === "models" && models[scrollIndex]) {
        const model = models[scrollIndex];
        if (!model.is_user_defined) { setStatus("Cannot delete built-in model"); return; }
        setConfirmAction({ message: `Delete model "${model.name}"?`, action: () => {
          api.removeModel(model.name).then((ok) => { setStatus(ok ? `Removed ${model.name}` : "Not found"); refresh(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        }});
        return;
      }
      if (section === "providers" && providers[scrollIndex]) {
        const prov = providers[scrollIndex];
        if (!prov.is_user_defined) { setStatus("Cannot delete built-in provider"); return; }
        setConfirmAction({ message: `Delete provider "${prov.name}"?`, action: () => {
          api.removeProvider(prov.name).then((ok) => { setStatus(ok ? `Removed ${prov.name}` : "Not found"); refresh(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        }});
        return;
      }
      if (section === "env") {
        const envKey = Object.keys(envVars)[scrollIndex];
        if (envKey) {
          setConfirmAction({ message: `Delete env var "${envKey}"?`, action: () => {
            const updated = { ...envVars }; delete updated[envKey];
            api.saveEnv(updated).then(() => { setEnvVars(updated); setStatus(`Removed ${envKey}`); })
              .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
          }});
        }
        return;
      }
      if (section === "mcp" && mcpNames[scrollIndex]) {
        const serverName = mcpNames[scrollIndex];
        setConfirmAction({ message: `Delete MCP server "${serverName}"?`, action: () => {
          const updated = { ...mcpServers }; delete updated[serverName];
          api.mcpSave({ mcpServers: updated }).then(() => { setMcpServers(updated); setStatus(`Removed ${serverName}`); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        }});
        return;
      }
    }

    if (input === "D" && section === "models" && models[scrollIndex]) {
      const model = models[scrollIndex];
      api.setDefaultModel(model.name).then((ok) => { setStatus(ok ? `Default \u2192 ${model.name}` : "Failed"); refresh(); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }

    if (input === "T" && section === "models") {
      const model = models[scrollIndex];
      setTesting(true); setStatus(`Testing ${model?.name ?? "default"}...`);
      api.testModel(model?.name).then((result) => {
        setTesting(false); setStatus(result.success ? `${result.output}` : `FAILED: ${result.output}`);
      }).catch((err) => { setTesting(false); setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); });
      return;
    }

    if (input === "t" && section === "network") {
      const updated = { ...networkConfig, enabled: !networkConfig.enabled };
      api.saveNetworkConfig(updated).then(() => { setNetworkConfig(updated); setStatus(`Network ${updated.enabled ? "enabled" : "disabled"}`); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
  });

  // Multi-step add flows (keep same logic, just condensed)
  const handleModelStep = useCallback((value: string) => {
    if (!addingModel) return;
    const trimmed = value.trim();
    if (!trimmed && addingModel.step !== "model_id") { setAddingModel(null); return; }
    if (addingModel.step === "provider_name") { setAddingModel({ ...addingModel, step: "provider_type", provider_name: trimmed }); setInputValue(""); }
    else if (addingModel.step === "provider_type") { setAddingModel({ ...addingModel, step: "model_name", provider_type: trimmed }); setInputValue(""); }
    else if (addingModel.step === "model_name") { setAddingModel({ ...addingModel, step: "model_id", model_name: trimmed }); setInputValue(trimmed); }
    else if (addingModel.step === "model_id") {
      const modelId = trimmed || addingModel.model_name;
      api.addModel({ provider_name: addingModel.provider_name, provider_type: addingModel.provider_type, model_name: addingModel.model_name, model: modelId })
        .then(() => { setStatus(`Added model ${addingModel.model_name}`); setAddingModel(null); refresh(); })
        .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setAddingModel(null); });
    }
  }, [addingModel, refresh]);

  const handleProviderStep = useCallback((value: string) => {
    if (!addingProvider) return;
    const trimmed = value.trim();
    if (!trimmed && addingProvider.step === "name") { setAddingProvider(null); return; }
    if (addingProvider.step === "name") { setAddingProvider({ ...addingProvider, step: "type", name: trimmed }); setInputValue(""); }
    else if (addingProvider.step === "type") { if (!trimmed) { setAddingProvider(null); return; } setAddingProvider({ ...addingProvider, step: "api_key", type: trimmed }); setInputValue(""); }
    else if (addingProvider.step === "api_key") { setAddingProvider({ ...addingProvider, step: "base_url", api_key: trimmed }); setInputValue(""); }
    else if (addingProvider.step === "base_url") {
      api.addProvider({ name: addingProvider.name, type: addingProvider.type, api_key: addingProvider.api_key || undefined, base_url: trimmed || undefined })
        .then(() => { setStatus(`Added provider ${addingProvider.name}`); setAddingProvider(null); refresh(); })
        .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setAddingProvider(null); });
    }
  }, [addingProvider, refresh]);

  const handleEnvStep = useCallback((value: string) => {
    if (!addingEnvVar) return;
    const trimmed = value.trim();
    if (!trimmed && addingEnvVar.step === "key") { setAddingEnvVar(null); return; }
    if (addingEnvVar.step === "key") { setAddingEnvVar({ step: "value", key: trimmed }); setInputValue(""); }
    else {
      const updated = { ...envVars, [addingEnvVar.key]: trimmed };
      api.saveEnv(updated).then(() => { setEnvVars(updated); setStatus(`Set ${addingEnvVar.key}`); setAddingEnvVar(null); })
        .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setAddingEnvVar(null); });
    }
  }, [addingEnvVar, envVars]);

  const handleMcpStep = useCallback((value: string) => {
    if (!addingMcp) return;
    const trimmed = value.trim();
    if (!trimmed && addingMcp.step === "name") { setAddingMcp(null); return; }
    if (addingMcp.step === "name") { setAddingMcp({ ...addingMcp, step: "type", name: trimmed }); setInputValue("stdio"); }
    else if (addingMcp.step === "type") {
      const mcpType = trimmed || "stdio";
      if (mcpType === "stdio") { setAddingMcp({ ...addingMcp, step: "command", type: mcpType }); setInputValue(""); }
      else { setAddingMcp({ ...addingMcp, step: "url", type: mcpType }); setInputValue(""); }
    }
    else if (addingMcp.step === "command") { if (!trimmed) { setAddingMcp(null); return; } setAddingMcp({ ...addingMcp, step: "args", command: trimmed }); setInputValue(""); }
    else if (addingMcp.step === "args") {
      const args = trimmed ? trimmed.split(",").map((s) => s.trim()) : [];
      const entry: McpServer = { type: "stdio", command: addingMcp.command, args };
      const updated = { ...mcpServers, [addingMcp.name]: entry };
      api.mcpSave({ mcpServers: updated }).then(() => { setMcpServers(updated); setStatus(`Added MCP server ${addingMcp.name}`); setAddingMcp(null); })
        .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setAddingMcp(null); });
    }
    else if (addingMcp.step === "url") {
      if (!trimmed) { setAddingMcp(null); return; }
      const entry: McpServer = { type: addingMcp.type, url: trimmed };
      const updated = { ...mcpServers, [addingMcp.name]: entry };
      api.mcpSave({ mcpServers: updated }).then(() => { setMcpServers(updated); setStatus(`Added MCP server ${addingMcp.name}`); setAddingMcp(null); })
        .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setAddingMcp(null); });
    }
  }, [addingMcp, mcpServers]);

  const cancelInput = useCallback(() => { setAddingModel(null); setAddingProvider(null); setAddingEnvVar(null); setAddingMcp(null); }, []);

  if (showHelp) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <HelpOverlay title="Settings" hints={HELP_HINTS} onClose={() => setShowHelp(false)} />
      </Box>
    );
  }

  // Input prompt for multi-step flows
  const renderInput = () => {
    if (addingModel) {
      const prompts: Record<string, { prompt: string; placeholder: string }> = {
        provider_name: { prompt: "Provider name:", placeholder: "e.g. openai" },
        provider_type: { prompt: `Provider type for "${addingModel.provider_name}":`, placeholder: "openai | azure | litellm" },
        model_name: { prompt: "Model name:", placeholder: "e.g. gpt-4o" },
        model_id: { prompt: "Model ID:", placeholder: addingModel.model_name },
      };
      const p = prompts[addingModel.step];
      return <TextInput value={inputValue} onChange={setInputValue} onSubmit={handleModelStep} onCancel={cancelInput} prompt={p.prompt} placeholder={p.placeholder} />;
    }
    if (addingProvider) {
      const prompts: Record<string, { prompt: string; placeholder: string }> = {
        name: { prompt: "Provider name:", placeholder: "e.g. my-openai" },
        type: { prompt: `Type for "${addingProvider.name}":`, placeholder: "openai | azure | litellm" },
        api_key: { prompt: "API key (enter to skip):", placeholder: "sk-..." },
        base_url: { prompt: "Base URL (enter to skip):", placeholder: "https://..." },
      };
      const p = prompts[addingProvider.step];
      return <TextInput value={inputValue} onChange={setInputValue} onSubmit={handleProviderStep} onCancel={cancelInput} prompt={p.prompt} placeholder={p.placeholder} />;
    }
    if (addingEnvVar) {
      const prompt = addingEnvVar.step === "key" ? "Variable name:" : `Value for ${addingEnvVar.key}:`;
      return <TextInput value={inputValue} onChange={setInputValue} onSubmit={handleEnvStep} onCancel={cancelInput} prompt={prompt} placeholder={addingEnvVar.step === "key" ? "MY_VAR" : ""} />;
    }
    if (addingMcp) {
      const prompts: Record<string, { prompt: string; placeholder: string }> = {
        name: { prompt: "Server name:", placeholder: "e.g. my-mcp-server" },
        type: { prompt: `Type for "${addingMcp.name}":`, placeholder: "stdio | sse | http" },
        command: { prompt: "Command:", placeholder: "e.g. npx" },
        args: { prompt: "Args (comma-separated):", placeholder: "-y,@mcp/server" },
        url: { prompt: "Server URL:", placeholder: "https://..." },
      };
      const p = prompts[addingMcp.step];
      return <TextInput value={inputValue} onChange={setInputValue} onSubmit={handleMcpStep} onCancel={cancelInput} prompt={p.prompt} placeholder={p.placeholder} />;
    }
    return null;
  };

  // Section-specific bar hints
  const getBarHints = (): Hint[] => {
    const base: Hint[] = [{ key: "o/i/m/p/M/n/e", label: "sections" }];
    if (section === "display") {
      base.push({ key: "j/k", label: "scroll" }, { key: "enter", label: "apply" });
    } else if (section !== "overview" && section !== "network") {
      base.push({ key: "j/k", label: "scroll" }, { key: "a", label: "add" }, { key: "d", label: "delete" });
    }
    if (section === "models") base.push({ key: "D", label: "default" }, { key: "T", label: "test" });
    if (section === "network") base.push({ key: "t", label: "toggle" });
    base.push({ key: "r", label: "refresh" }, { key: "?", label: "help" }, { key: "q", label: "quit" });
    return base;
  };

  return (
    <Box flexDirection="column" flexGrow={1}>
      <Box flexDirection="row" flexGrow={1}>
        {/* Section nav */}
        <Panel title="Settings" width={20}>
          <Box flexDirection="column">
            {SECTIONS.map((s) => {
              const isActive = section === s.id;
              const count = s.id === "models" ? ` (${models.length})` : s.id === "providers" ? ` (${providers.length})` : s.id === "mcp" ? ` (${mcpNames.length})` : "";
              return (
                <Text key={s.id} color={isActive ? t.accent : undefined} bold={isActive}>
                  {isActive ? "\u25B8 " : "  "}
                  <Text color={t.accent}>{s.key}</Text> {s.label}{count}
                </Text>
              );
            })}
          </Box>
        </Panel>

        {/* Section content */}
        <Panel title={SECTIONS.find((s) => s.id === section)?.label ?? section} focused flexGrow={1}>
          <Box flexDirection="column" paddingX={1}>
            {section === "overview" && data ? (
              <>
                <Box><Text color={t.fgSubtle}>Config  </Text><Text color={t.fg}>{data.config_dir}</Text></Box>
                <Box><Text color={t.fgSubtle}>Projects</Text><Text color={t.fg}> {data.projects_count}</Text></Box>
                <Box><Text color={t.fgSubtle}>Tickets </Text><Text color={t.fg}> {data.tickets_count}</Text></Box>
                <Box><Text color={t.fgSubtle}>Tasks   </Text><Text color={t.fg}> {data.tasks_count}</Text></Box>
              </>
            ) : section === "overview" ? <Text color={t.fgSubtle}>Loading...</Text> : null}

            {section === "display" ? (
              <Box flexDirection="column">
                <Box marginBottom={1}><Text color={t.fg} bold>Theme</Text></Box>
                {themeNames.map((name, i) => {
                  const isSelected = i === scrollIndex;
                  const isCurrent = name === themeName;
                  return (
                    <Text key={name} color={isSelected ? t.accent : t.fg}>
                      {isSelected ? "\u25B8 " : "  "}{name}
                      {isCurrent ? <Text color={t.success}> (active)</Text> : null}
                    </Text>
                  );
                })}
                <Box marginTop={1}><Text color={t.fgSubtle}>j/k navigate · enter apply</Text></Box>
              </Box>
            ) : null}

            {section === "models" ? (
              models.length === 0 ? <Text color={t.fgSubtle}>No models. Press a to add.</Text> : (
                models.slice(Math.max(0, scrollIndex - 2), scrollIndex + visibleRows).map((m, i) => {
                  const idx = Math.max(0, scrollIndex - 2) + i;
                  const isSelected = idx === scrollIndex;
                  return (
                    <Text key={m.name} color={isSelected ? t.accent : t.fg} wrap="truncate">
                      {isSelected ? "\u25B8 " : "  "}{m.name}
                      {m.is_default ? <Text color={t.success}> (default)</Text> : null}
                      {m.is_voice_default ? <Text color={t.info}> (voice)</Text> : null}
                      {m.is_user_defined ? <Text color={t.warning}> *</Text> : null}
                      <Text color={t.fgSubtle}> [{m.provider}] {m.model ?? ""}</Text>
                    </Text>
                  );
                })
              )
            ) : null}

            {section === "providers" ? (
              providers.length === 0 ? <Text color={t.fgSubtle}>No providers. Press a to add.</Text> : (
                providers.slice(Math.max(0, scrollIndex - 2), scrollIndex + visibleRows).map((p, i) => {
                  const idx = Math.max(0, scrollIndex - 2) + i;
                  const isSelected = idx === scrollIndex;
                  return (
                    <Text key={p.name} color={isSelected ? t.accent : undefined} wrap="truncate">
                      {isSelected ? "\u25B8 " : "  "}{p.name}
                      <Text color={t.fgSubtle}> [{p.type}] {p.model_count} models</Text>
                      {p.has_api_key ? <Text color={t.success}> key\u2713</Text> : <Text color={t.error}> no key</Text>}
                      {p.is_user_defined ? <Text color={t.warning}> *</Text> : null}
                    </Text>
                  );
                })
              )
            ) : null}

            {section === "mcp" ? (
              mcpNames.length === 0 ? <Text color={t.fgSubtle}>No MCP servers. Press a to add.</Text> : (
                mcpNames.map((name, i) => {
                  const server = mcpServers[name]!;
                  const isSelected = i === scrollIndex;
                  const sType = server.type ?? "stdio";
                  const summary = sType === "stdio" ? `${server.command ?? ""}${server.args?.length ? " " + server.args.join(" ") : ""}` : server.url ?? "";
                  return (
                    <Text key={name} color={isSelected ? t.accent : undefined} wrap="truncate">
                      {isSelected ? "\u25B8 " : "  "}{name}
                      <Text color={t.fgSubtle}> [{sType}] {summary}</Text>
                    </Text>
                  );
                })
              )
            ) : null}

            {section === "network" ? (
              <>
                <Box>
                  <Text color={t.fgSubtle}>Enabled         </Text>
                  <Text color={networkConfig.enabled ? t.success : t.error} bold>{networkConfig.enabled ? "YES" : "NO"}</Text>
                </Box>
                <Box>
                  <Text color={t.fgSubtle}>Allow private   </Text>
                  <Text color={t.fg}>{networkConfig.allow_private_ips ? "yes" : "no"}</Text>
                </Box>
                <Box marginTop={1} flexDirection="column">
                  <Text color={t.fgSubtle}>Allowlist:</Text>
                  {(networkConfig.allowlist ?? []).length === 0 ? <Text color={t.fgSubtle}>  (empty)</Text> : (networkConfig.allowlist ?? []).map((h, i) => <Text key={i} color={t.fg}>  {h}</Text>)}
                </Box>
                <Box marginTop={1} flexDirection="column">
                  <Text color={t.fgSubtle}>Denylist:</Text>
                  {(networkConfig.denylist ?? []).length === 0 ? <Text color={t.fgSubtle}>  (empty)</Text> : (networkConfig.denylist ?? []).map((h, i) => <Text key={i} color={t.fg}>  {h}</Text>)}
                </Box>
              </>
            ) : null}

            {section === "env" ? (
              Object.keys(envVars).length === 0 ? <Text color={t.fgSubtle}>No env vars. Press a to add.</Text> : (
                Object.entries(envVars).map(([key, value], i) => {
                  const isSelected = i === scrollIndex;
                  return (
                    <Text key={key} color={isSelected ? t.accent : undefined} wrap="truncate">
                      {isSelected ? "\u25B8 " : "  "}
                      <Text color={t.warning}>{key}</Text><Text color={t.fgSubtle}>=</Text><Text color={t.fg}>{value}</Text>
                    </Text>
                  );
                })
              )
            ) : null}

            {renderInput()}
          </Box>
        </Panel>
      </Box>

      {confirmAction ? (
        <Box paddingX={1}>
          <ConfirmDialog message={confirmAction.message} onConfirm={() => { confirmAction.action(); setConfirmAction(null); }} onCancel={() => setConfirmAction(null)} />
        </Box>
      ) : null}

      <StatusBar hints={getBarHints()} status={status} />
    </Box>
  );
}
