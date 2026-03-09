import React, { useCallback, useState } from "react";
import { Box, Text, useInput, type Key } from "ink";
import { api } from "../rpc.js";
import { TextInput } from "../components/TextInput.js";

type Props = {
  onComplete: () => void;
};

type Step = "welcome" | "provider_name" | "provider_type" | "api_key" | "base_url" | "model_name" | "model_id" | "testing" | "done";

export function Onboarding({ onComplete }: Props) {
  const [step, setStep] = useState<Step>("welcome");
  const [providerName, setProviderName] = useState("");
  const [providerType, setProviderType] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelName, setModelName] = useState("");
  const [inputValue, setInputValue] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useInput((input: string, key: Key) => {
    if (step === "welcome") {
      if (key.return) {
        setStep("provider_type");
        setInputValue("openai");
      }
      if (input === "q") {
        onComplete();
      }
      return;
    }
    if (step === "done") {
      if (key.return || input === "q") {
        onComplete();
      }
    }
  });

  const handleStep = useCallback(async (value: string) => {
    const trimmed = value.trim();

    if (step === "provider_type") {
      if (!trimmed) return;
      setProviderType(trimmed);
      // Default provider name to provider type
      setProviderName(trimmed);
      setStep("api_key");
      setInputValue("");
    } else if (step === "api_key") {
      if (!trimmed) {
        setError("API key is required");
        return;
      }
      setApiKey(trimmed);
      if (providerType === "openai-compatible") {
        setStep("base_url");
        setInputValue("");
      } else {
        setStep("model_name");
        setInputValue(providerType === "openai" ? "gpt-4o" : providerType === "anthropic" ? "claude-sonnet-4-20250514" : "");
      }
    } else if (step === "base_url") {
      setBaseUrl(trimmed);
      setStep("model_name");
      setInputValue("");
    } else if (step === "model_name") {
      if (!trimmed) return;
      setModelName(trimmed);
      setStep("model_id");
      setInputValue(trimmed);
    } else if (step === "model_id") {
      const modelId = trimmed || modelName;
      setStep("testing");
      setStatus("Setting up provider and model...");
      setError(null);

      try {
        // Create provider
        await api.addProvider({
          name: providerName,
          type: providerType,
          api_key: apiKey || undefined,
          base_url: baseUrl || undefined,
        });

        // Create model
        await api.addModel({
          provider_name: providerName,
          provider_type: providerType,
          model_name: modelName,
          model: modelId,
          set_default: true,
        });

        // Test connection
        setStatus("Testing connection...");
        const result = await api.testModel(modelName);
        if (result.success) {
          setStatus(result.output);
          setStep("done");
        } else {
          setStatus(null);
          setError(`Connection test failed: ${result.output}\nModel was saved anyway — you can fix settings later.`);
          setStep("done");
        }
      } catch (err) {
        setStatus(null);
        setError(`Setup failed: ${err instanceof Error ? err.message : String(err)}`);
        setStep("done");
      }
    }
  }, [step, providerName, providerType, apiKey, baseUrl, modelName]);

  const cancelInput = useCallback(() => {
    // Go back one step
    if (step === "api_key") { setStep("provider_type"); setInputValue(providerType); }
    else if (step === "base_url") { setStep("api_key"); setInputValue(apiKey); }
    else if (step === "model_name") { setStep("api_key"); setInputValue(apiKey); }
    else if (step === "model_id") { setStep("model_name"); setInputValue(modelName); }
    else { onComplete(); }
  }, [step, providerType, apiKey, modelName, onComplete]);

  return (
    <Box flexDirection="column" paddingX={2} paddingY={1}>
      <Box marginBottom={1}>
        <Text bold color="cyan">Omni Code Setup</Text>
      </Box>

      {step === "welcome" ? (
        <Box flexDirection="column">
          <Text>No model credentials configured yet.</Text>
          <Text />
          <Text>This wizard will help you set up your first LLM provider and model.</Text>
          <Text>You can add more later in <Text color="cyan">Settings → Models</Text>.</Text>
          <Text />
          <Text>Press <Text color="cyan" bold>enter</Text> to begin, or <Text color="gray">q</Text> to skip.</Text>
        </Box>
      ) : null}

      {step === "provider_type" ? (
        <Box flexDirection="column">
          <Text color="gray">Step 1/4</Text>
          <Text bold>Provider type</Text>
          <Text />
          <TextInput
            value={inputValue}
            onChange={setInputValue}
            onSubmit={handleStep}
            onCancel={cancelInput}
            prompt="Type:"
            placeholder="openai | anthropic | azure | litellm | openai-compatible"
          />
        </Box>
      ) : null}

      {step === "api_key" ? (
        <Box flexDirection="column">
          <Text color="gray">Step 2/4 — Provider: {providerType}</Text>
          <Text bold>API Key</Text>
          <Text />
          <TextInput
            value={inputValue}
            onChange={setInputValue}
            onSubmit={handleStep}
            onCancel={cancelInput}
            prompt="API key:"
            placeholder="sk-..."
          />
        </Box>
      ) : null}

      {step === "base_url" ? (
        <Box flexDirection="column">
          <Text color="gray">Step 2b/4 — Provider: {providerType}</Text>
          <Text bold>Base URL</Text>
          <Text />
          <TextInput
            value={inputValue}
            onChange={setInputValue}
            onSubmit={handleStep}
            onCancel={cancelInput}
            prompt="Base URL:"
            placeholder="https://api.example.com/v1"
          />
        </Box>
      ) : null}

      {step === "model_name" ? (
        <Box flexDirection="column">
          <Text color="gray">Step 3/4 — Provider: {providerType}</Text>
          <Text bold>Model name (your label)</Text>
          <Text />
          <TextInput
            value={inputValue}
            onChange={setInputValue}
            onSubmit={handleStep}
            onCancel={cancelInput}
            prompt="Model name:"
            placeholder="e.g. gpt-4o, claude-sonnet"
          />
        </Box>
      ) : null}

      {step === "model_id" ? (
        <Box flexDirection="column">
          <Text color="gray">Step 4/4 — Model: {modelName}</Text>
          <Text bold>Model ID (enter to use name as ID)</Text>
          <Text />
          <TextInput
            value={inputValue}
            onChange={setInputValue}
            onSubmit={handleStep}
            onCancel={cancelInput}
            prompt="Model ID:"
            placeholder={modelName}
          />
        </Box>
      ) : null}

      {step === "testing" ? (
        <Box flexDirection="column">
          <Text color="yellow">{status}</Text>
        </Box>
      ) : null}

      {step === "done" ? (
        <Box flexDirection="column">
          {error ? (
            <Text color="red">{error}</Text>
          ) : (
            <>
              <Text color="green" bold>Setup complete!</Text>
              <Text />
              <Text>{status}</Text>
            </>
          )}
          <Text />
          <Text>Press <Text color="cyan" bold>enter</Text> to continue.</Text>
        </Box>
      ) : null}
    </Box>
  );
}
