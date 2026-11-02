import { AgentPanel } from "./AgentPanel";
import { ShareButton, ArtifactFeedback } from "./Sharing";
import { useEffect, useState } from "react";
import type { Presentation } from "@omniagents/chat-ui/lib/presentation";
import { Button } from "@omniagents/ui/button";
import { ArrowLeft, X } from "lucide-react";
import { ArtifactContent } from "./Library";
import { CanvasView } from "./Canvas";
import { Desktop } from "./Desktop";
import { library, type LibraryArtifact, type Person } from "./api";

function SavedArtifact({ id, account }: { id: string; account: Person }) {
  const [item, setItem] = useState<LibraryArtifact>();
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const next = await library.get(id);
        if (!disposed) {
          // The API issues a fresh view URL per request. Keep the iframe alive
          // unless its files changed, preserving interactions and scroll.
          setItem((previous) =>
            previous?.updated_at === next.updated_at ? previous : next,
          );
          setError("");
        }
      } catch (e) {
        if (!disposed)
          setError(e instanceof Error ? e.message : "Unable to open artifact");
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [id]);
  if (error)
    return (
      <p role="alert" className="p-4">
        {error}
      </p>
    );
  return item ? (
    <>
      {(account.admin || item.owner_id === account.id) && (
        <div className="p-2">
          <ShareButton kind="artifact" id={id} />
        </div>
      )}
      <ArtifactContent artifact={item} embedded />
      <ArtifactFeedback id={id} account={account} compact />
    </>
  ) : (
    <p role="status" className="p-4">
      Opening…
    </p>
  );
}

export function ChatViewer({
  value,
  chatId,
  tab,
  onTab,
  account,
  onClose,
  onPresent,
}: {
  value: Presentation;
  chatId: string;
  tab: string;
  onTab: (tab: string) => void;
  account: Person;
  onClose: () => void;
  onPresent: (value: Presentation) => void;
}) {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (value.kind !== "canvas") return;
    const timer = setInterval(() => setVersion((v) => v + 1), 3000);
    return () => clearInterval(timer);
  }, [value.kind]);
  return (
    <section
      aria-label={value.title}
      className="flex h-full min-h-0 min-w-0 flex-col bg-background"
    >
      {value.kind !== "activity" && <header className="flex h-12 shrink-0 items-center justify-between gap-2 border-b px-3">
        <div className="flex min-w-0 items-center gap-2">
          {<Button variant="ghost" size="icon-sm" aria-label="Back to agent panel" onClick={() => onPresent({kind: "activity", title: "Activity", id: chatId, presentation_id: crypto.randomUUID(), issued_at: Date.now()})}><ArrowLeft /></Button>}
          <span className="truncate text-sm font-medium">{value.title}</span>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Close viewer"
          onClick={onClose}
        >
          <X />
        </Button>
      </header>}
      <div className="flex min-h-0 flex-1 flex-col overflow-auto">
        {value.kind === "activity" ? (
          <AgentPanel chatId={chatId} tab={tab} onTab={onTab} onClose={onClose} />
        ) : value.kind === "desktop" ? (
          <Desktop />
        ) : value.kind === "artifact" ? (
          <SavedArtifact key={value.id} id={value.id!} account={account} />
        ) : (
          <CanvasView
            key={`${value.id}:${value.board ?? ""}`}
            id={value.id!}
            board={value.board}
            account={account}
            version={version}
            navigate={(path) => {
              if (path === "/canvas") onClose();
              else {
                const id = path.split("/")[2];
                if (id)
                  onPresent({
                    ...value,
                    id: decodeURIComponent(id),
                    board: undefined,
                  });
              }
            }}
          />
        )}
      </div>
    </section>
  );
}
