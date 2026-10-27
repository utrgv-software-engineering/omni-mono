import { useMemo, useRef, useState } from "react";

import { Button } from "@omniagents/ui/button";
import { Paperclip, X } from "lucide-react";

import type { SlackUser } from "./api";
import { MentionsContext, MentionTextarea } from "./Mentions";

/** The message box. Enter sends, Shift+Enter is a new line; @name mentions;
 * a file goes with the text as its comment. */
export function Composer({
  placeholder,
  users,
  onSend,
  onAttach,
}: {
  placeholder: string;
  users: SlackUser[];
  onSend: (text: string) => Promise<void>;
  onAttach?: (file: File, comment: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const people = useMemo(
    () =>
      users
        .filter((u) => !u.deleted)
        .map((u) => ({ login: u.name, name: u.real_name })),
    [users],
  );

  const submit = async () => {
    const body = text.trim();
    if ((!body && !file) || sending) return;
    setSending(true);
    try {
      if (file && onAttach) await onAttach(file, body);
      else await onSend(body);
      setText("");
      setFile(null);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="shrink-0 border-t p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      {file && (
        <div className="mb-2 flex w-fit items-center gap-2 rounded-md border px-2 py-1 text-xs">
          <Paperclip className="size-3" /> {file.name}
          <button
            type="button"
            title="Remove the file"
            onClick={() => setFile(null)}
          >
            <X className="size-3" />
          </button>
        </div>
      )}
      <MentionsContext.Provider value={people}>
        <MentionTextarea
          value={text}
          onValue={setText}
          rows={2}
          above
          placeholder={placeholder}
          disabled={sending}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          className="min-h-0 resize-none"
        />
      </MentionsContext.Provider>
      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
        {onAttach && (
          <>
            <input
              ref={picker}
              type="file"
              className="hidden"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                e.target.value = "";
              }}
            />
            <Button
              variant="ghost"
              size="icon-sm"
              title="Attach a file"
              onClick={() => picker.current?.click()}
            >
              <Paperclip />
            </Button>
          </>
        )}
        <span>
          <kbd>Enter</kbd> to send · <kbd>Shift+Enter</kbd> for a new line ·{" "}
          <code>*bold*</code> <code>_italic_</code> <code>`code`</code> · @name
          to mention
        </span>
      </div>
    </div>
  );
}
