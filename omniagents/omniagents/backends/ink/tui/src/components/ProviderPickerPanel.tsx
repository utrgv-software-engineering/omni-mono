import React, { useEffect, useRef, useState } from "react";
import { Box, Text } from "ink";
import { useImmediateState } from "../hooks/useImmediateState.js";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import { SelectableRow, RowText } from "./shared/SelectableRow.js";
import { windowStart } from "./model-picker-model.js";
import { Theme } from "../theme.js";

type Provider = { name: string; model: string; label: string };
export function ProviderPickerPanel({
  client,
  sessionId,
  onClose,
}: {
  client: RPCClient | null;
  sessionId?: string;
  onClose: () => void;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [index, setIndex, indexRef] = useImmediateState(0);
  const [confirmation, setConfirmation, confirmationRef] =
    useImmediateState<Provider | null>(null);
  const [message, setMessage] = useState("Loading providers…");
  const busy = useRef(false);
  const generation = useRef(0);
  const load = async () => {
    if (!client || busy.current) return;
    const request = ++generation.current;
    setConfirmation(null);
    setMessage("Loading providers…");
    setProviders([]);
    try {
      const result = await client.serverCall("model_providers", {}, sessionId);
      if (generation.current !== request) return;
      const rows = (result.providers ?? []) as Provider[];
      setProviders(rows);
      setCurrent(result.current as string | null);
      setIndex(
        Math.max(
          0,
          rows.findIndex((p) => p.name === result.current),
        ),
      );
      setMessage(
        result.error
          ? String(result.error)
          : rows.length
            ? ""
            : "No providers with text models configured.",
      );
    } catch (error) {
      if (generation.current === request) setMessage(String(error));
    }
  };
  useEffect(() => {
    void load();
    return () => {
      generation.current++;
    };
  }, [client, sessionId]);
  const change = async () => {
    const target = confirmationRef.current;
    if (!client || !target || busy.current) return;
    busy.current = true;
    const request = generation.current;
    setMessage("Changing provider…");
    try {
      const result = await client.serverCall(
        "switch_provider",
        {
          model: target.model,
          expected_provider: current,
          confirm: true,
        },
        sessionId,
      );
      if (generation.current !== request) return;
      if (!result.ok) {
        setConfirmation(null);
        setMessage(
          String(
            result.error ?? "Provider change failed. Refresh and try again.",
          ),
        );
      } else {
        onClose();
      }
    } catch (error) {
      if (generation.current === request) {
        setConfirmation(null);
        setMessage(String(error));
      }
    } finally {
      busy.current = false;
    }
  };
  useKeypress(
    (input, key) => {
      const confirmation = confirmationRef.current;
      const index = indexRef.current;
      if (key.paste) return true;
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (busy.current) return true;
      if (key.escape || input === "q" || (confirmation && input === "n")) {
        if (confirmation) setConfirmation(null);
        else onClose();
        return true;
      }
      if (confirmation) {
        if (input === "y") void change();
        return true;
      }
      if (input === "r") {
        void load();
        return true;
      }
      if (key.upArrow || input === "k")
        setIndex((value) => Math.max(0, value - 1));
      if (key.downArrow || input === "j")
        setIndex((value) => Math.min(providers.length - 1, value + 1));
      if (key.return && providers[index]) {
        if (providers[index].name === current) onClose();
        else {
          setMessage("");
          setConfirmation(providers[index]);
        }
      }
      return true;
    },
    { isActive: true, priority: KeypressPriority.High },
  );
  const start = windowStart(index, providers.length, 8);
  return (
    <Box
      flexDirection="column"
      paddingX={1}
      borderStyle="round"
      borderColor={Theme.stateActive}
    >
      <Text>Workspace provider · current: {current ?? "none"}</Text>
      {confirmation ? (
        <>
          <Text>
            Switch to {confirmation.name} ({confirmation.label}) for all
            projects and agents?
          </Text>
          <Text>
            Continuing conversations will send their history to{" "}
            {confirmation.name}.
          </Text>
          <Text>
            Active runs keep their current provider. New runs use this
            selection.
          </Text>
          <Text>y confirm · n/esc cancel</Text>
        </>
      ) : (
        <>
          {providers.slice(start, start + 8).map((provider, offset) => (
            <SelectableRow
              key={provider.name}
              selected={start + offset === index}
            >
              <RowText>
                {provider.name} · {provider.label}
                {provider.name === current ? " (current)" : ""}
              </RowText>
            </SelectableRow>
          ))}
          <Text>↑↓ select · enter choose · r refresh · esc models</Text>
        </>
      )}
      {message && <Text color={Theme.stateBlocked}>{message}</Text>}
    </Box>
  );
}
