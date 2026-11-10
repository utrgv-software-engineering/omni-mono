import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSelector } from "@xstate/react";
import { useRPCClient, useRPCConnected } from "@omniagents/chat-ui/rpc-context";
import {
  useChatSession,
  useSessionField,
} from "@omniagents/chat-ui/hooks/use-chat-session";
import { useRealtimeVoice } from "@omniagents/chat-ui/hooks/use-realtime-voice";
import { MessageList } from "@omniagents/chat-ui/components/MessageList";
import { Input } from "@omniagents/chat-ui/components/Input";
import { OmniAvatar } from "@omniagents/chat-ui/components/omni-avatar/OmniAvatar";
import { agentPalette } from "@omniagents/chat-ui/components/omni-avatar/agent-palette";
import { ModelSessionControls } from "@omniagents/chat-ui/components/ModelSessionControls";
import { EscalationBanner } from "@omniagents/chat-ui/components/EscalationBanner";
import {
  presentationFromArtifact,
  PresentationGate,
  type Presentation,
} from "@omniagents/chat-ui/lib/presentation";
import type { Attachment, ChatMessage } from "@omniagents/client/transcript/chat-types";
import { Button } from "@omniagents/ui/button";
import { factoryFetch } from "./factory-scope";

/** The chat header's top padding (``py-2`` in Personal.tsx): the avatar's
 * planet starts there too, like the header's other items. */
const HEADER_PADDING = 8;

export type PublishedMessage = {
  seq: number;
  id: string;
  role: "user" | "assistant";
  author: string;
  /** The admin's display name, for a person's message in a shared chat. */
  author_name?: string;
  text: string;
  created_at: number;
  content?: Array<{
    type: string;
    image_url?: string;
    file_data?: string;
    filename?: string;
  }>;
};

/** This projection accepts public-message records only. It has no transcript input. */
export function publicMessageItems(
  messages: PublishedMessage[],
): ChatMessage[] {
  return messages.map((message) => ({
    type: "chat",
    role: message.role,
    content: message.text,
    message_id: String(message.seq),
    ...(message.role === "user"
      ? { sender: { name: message.author_name || message.author } }
      : {}),
    attachments: Array.isArray(message.content)
      ? message.content.flatMap<Attachment>((part) => {
          if (part.type === "input_image" && part.image_url)
            return [{ type: "image" as const, url: part.image_url }];
          if (part.type === "input_file" && part.file_data)
            return [
              {
                type: "file" as const,
                url: `data:application/octet-stream;base64,${part.file_data}`,
                filename: part.filename,
              },
            ];
          return [];
        })
      : undefined,
    timestamp: new Date(message.created_at * 1000).toISOString(),
  }));
}

export function PublishedChat({
  sessionId,
  active,
  showAvatar = true,
  agentName = "Agent",
  agentId,
  onPresent,
}: {
  sessionId: string;
  active: boolean;
  showAvatar?: boolean;
  agentName?: string;
  /** The agent's ID: its avatar's colours, as in the chat list. */
  agentId?: string;
  onPresent: (value: Presentation) => void;
}) {
  const client = useRPCClient();
  const connected = useRPCConnected();
  const runtime = useChatSession(client, sessionId);
  const session = runtime.controller;
  const voice = useRealtimeVoice();
  const voiceLive = useSelector(voice.actor, (state) => state.matches("live"));
  const voiceMuted = useSelector(voice.actor, (state) => state.context.muted);
  const [escalation] = useSessionField(session, "escalation");
  const [messages, setMessages] = useState<PublishedMessage[]>([]);
  const [feedError, setFeedError] = useState("");
  const [runtimeError, setRuntimeError] = useState("");
  // The newest seq read; ``null`` until the chat's newest page is in.
  const cursor = useRef<number | null>(null);
  // Where earlier messages continue (``null``: the chat's first is shown).
  const earlier = useRef<number | null>(null);
  const [earlierHistory, setEarlierHistory] = useState<"none" | "more" | "loading">("none");
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const presentRef = useRef(onPresent);
  presentRef.current = onPresent;
  const activity = useCallback(
    () =>
      presentRef.current({
        kind: "activity",
        title: "Activity",
        id: sessionId,
        presentation_id: crypto.randomUUID(),
        issued_at: Date.now(),
      }),
    [sessionId],
  );

  useEffect(() => {
    let stopped = false,
      loading = false;
    const refresh = async () => {
      if (loading || stopped) return;
      loading = true;
      try {
        const feed = `/workplace/api/personal/chats/${encodeURIComponent(sessionId)}/feed`;
        const opening = cursor.current === null;
        if (opening) {
          // Open on the newest messages; older ones load on scrolling up.
          const response = await factoryFetch(`${feed}?latest=true`);
          if (!response.ok) throw new Error("Unable to refresh messages");
          const page = await response.json();
          if (stopped) return;
          setMessages(page.messages);
          cursor.current = page.cursor;
          earlier.current = page.earlier;
          setEarlierHistory(page.earlier ? "more" : "none");
        }
        // Then on from the newest read, each refresh.
        let more = cursor.current !== null && !opening;
        while (more && !stopped) {
          const response = await factoryFetch(`${feed}?after=${cursor.current}`);
          if (!response.ok) throw new Error("Unable to refresh messages");
          const page = await response.json();
          if (stopped) return;
          setMessages((previous) => [...previous, ...page.messages]);
          cursor.current = page.cursor;
          more = page.has_more;
        }
        setFeedError("");
      } catch (e) {
        if (!stopped)
          setFeedError(
            e instanceof Error ? e.message : "Unable to refresh messages",
          );
      } finally {
        loading = false;
      }
    };
    refreshRef.current = refresh;
    void refresh();
    const timer = setInterval(() => {
      if (active && !document.hidden) void refresh();
    }, 1000);
    const visible = () => {
      if (!document.hidden) void refresh();
    };
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      stopped = true;
      clearInterval(timer);
      window.removeEventListener("focus", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [sessionId, active]);

  useEffect(() => {
    void client.connect().catch(() => {});
  }, [client]);
  useEffect(() => {
    if (!connected) return;
    let stopped = false;
    void session
      .load()
      .then(() => {
        if (!stopped) setRuntimeError("");
      })
      .catch((e) => {
        if (!stopped) setRuntimeError(String(e.message || e));
      });
    return () => {
      stopped = true;
    };
  }, [connected, session]);
  useEffect(() => {
    if (!connected) return;
    const gate = new PresentationGate(Date.now());
    return session.on("client_request", (event) => {
      if (event.function !== "ui.add_artifact") return;
      const value = presentationFromArtifact(event.args ?? {});
      if (value && gate.accept(value, active)) presentRef.current(value);
    });
  }, [session, connected, active]);
  const lastAttention = useRef("");
  useEffect(() => {
    const key = [...runtime.pendingApprovals.keys()].join(",");
    if (key && key !== lastAttention.current && active) activity();
    lastAttention.current = key;
  }, [runtime.pendingApprovals, active, activity]);

  const loadEarlier = useCallback(async () => {
    const before = earlier.current;
    if (!before) return;
    setEarlierHistory("loading");
    try {
      const response = await factoryFetch(
        `/workplace/api/personal/chats/${encodeURIComponent(sessionId)}/feed?before=${before}`,
      );
      if (!response.ok) throw new Error("Unable to load earlier messages");
      const page = await response.json();
      if (earlier.current !== before) return; // Another chat, or already read.
      setMessages((previous) => {
        const shown = new Set(previous.map((message) => message.seq));
        return [...page.messages.filter((message: PublishedMessage) => !shown.has(message.seq)), ...previous];
      });
      earlier.current = page.earlier;
    } catch (e) {
      setFeedError(e instanceof Error ? e.message : "Unable to load earlier messages");
    } finally {
      setEarlierHistory(earlier.current ? "more" : "none");
    }
  }, [sessionId]);
  const items = useMemo(() => publicMessageItems(messages), [messages]);
  const palette = useMemo(() => (agentId ? agentPalette(agentId) : undefined), [agentId]);
  const send = async (text: string, files?: File[], inputId?: string) => {
    try {
      await session.send(text, files, { inputId });
      setRuntimeError("");
      await refreshRef.current();
    } catch (e) {
      setRuntimeError(e instanceof Error ? e.message : "Message not sent");
      throw e;
    }
  };
  const ready =
    connected && !["initializing", "initError"].includes(runtime.phase);
  return (
    <div className="flex h-full min-h-0 flex-col" data-public-chat={sessionId}>
      <div className="relative flex min-h-0 flex-1 flex-col">
        {showAvatar && <OmniAvatar name={agentName} palette={palette} top={HEADER_PADDING}
          online={connected}
          waiting={!!escalation || runtime.pendingApprovals.size > 0}
          working={runtime.thinking}
          voice={voice}
          onClick={activity}
        />}
        {/* Messages pass under the header and avatar: hidden under the header
            row, fading back in by the foot of the avatar's name. */}
        <div className="flex min-h-0 flex-1 flex-col [mask-image:linear-gradient(to_bottom,transparent_48px,black_112px)] [&_[data-testid=chat-transcript]]:pt-[104px]">
          <MessageList
            items={items}
            thinking={false}
            earlierHistory={earlierHistory}
            onLoadEarlier={loadEarlier}
          />
        </div>
      </div>
      {(feedError || runtimeError || runtime.phase === "initError") && (
        <div role="alert" className="flex items-center gap-2 px-3 py-2 text-sm">
          {feedError || runtimeError || runtime.error}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              void refreshRef.current();
              void session
                .load({ force: true })
                .then(() => setRuntimeError(""))
                .catch((e) => setRuntimeError(e.message));
            }}
          >
            Retry
          </Button>
        </div>
      )}
      {!connected && (
        <div role="status" className="px-3 text-sm text-muted-foreground">
          Reconnecting…
        </div>
      )}
      <EscalationBanner escalation={escalation} />
      <div className="flex justify-end px-3">
        <ModelSessionControls
          sessionId={sessionId}
          session={session}
          transport={client}
          connected={connected}
          disabled={!ready}
        />
      </div>
      <Input
        conversationId={sessionId}
        disabled={!ready}
        thinking={runtime.thinking}
        onSubmit={send}
        onStop={() => {
          void session.stopRun().catch((e) => setRuntimeError(e.message));
        }}
        voiceEnabled
        onVoiceStart={() => voice.open(sessionId)}
        onVoiceEnd={voice.close}
        onVoiceToggleMute={voice.toggleMute}
        voiceMuted={voiceMuted}
        voiceLive={voiceLive}
        voiceCaptions={false}
      />
    </div>
  );
}
