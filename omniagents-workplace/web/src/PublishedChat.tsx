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

export type PublishedMessage = {
  seq: number;
  id: string;
  role: "user" | "assistant";
  author: string;
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
  onPresent,
}: {
  sessionId: string;
  active: boolean;
  showAvatar?: boolean;
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
  const cursor = useRef(0);
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
        let more = true;
        while (more && !stopped) {
          const response = await factoryFetch(
            `/workplace/api/personal/chats/${encodeURIComponent(sessionId)}/feed?after=${cursor.current}`,
          );
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

  const items = useMemo(() => publicMessageItems(messages), [messages]);
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
      <div className="relative flex min-h-0 flex-1 flex-col [&_[data-testid=chat-transcript]]:pt-[120px]">
        {showAvatar && <OmniAvatar
          online={connected}
          waiting={!!escalation || runtime.pendingApprovals.size > 0}
          working={runtime.thinking}
          voice={voice}
          onClick={activity}
        />}
        <MessageList items={items} thinking={false} />
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
