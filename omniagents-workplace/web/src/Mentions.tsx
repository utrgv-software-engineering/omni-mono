// @-mentions as GitHub offers them: type @ in a text box and pick someone
// from the list that follows what you type.
import {
  createContext,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Textarea } from "@omniagents/ui/textarea";

export interface Mentionable {
  /** What follows the @: a GitHub login, a Slack or Linear username. */
  login: string;
  name: string;
  /** An avatar image; otherwise the name's initial. */
  avatar?: string;
}

/** Who can be mentioned: each service's view provides its people (and apps). */
export const MentionsContext = createContext<Mentionable[]>([]);

const MAX = 8;

/** The @word the caret is in, if any: where it starts, and what is typed after @. */
function mentionAt(text: string, caret: number) {
  const m = /(^|[^\w`])@([A-Za-z0-9._-]*)$/.exec(text.slice(0, caret));
  return m
    ? { start: caret - m[2].length - 1, query: m[2].toLowerCase() }
    : null;
}

export function MentionTextarea({
  value,
  onValue,
  above = false,
  ...props
}: Omit<React.ComponentProps<typeof Textarea>, "value" | "onChange"> & {
  value: string;
  onValue: (value: string) => void;
  /** Open the list above the box (one at the bottom of the window). */
  above?: boolean;
}) {
  const people = useContext(MentionsContext);
  const box = useRef<HTMLTextAreaElement>(null);
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [closed, setClosed] = useState(false);
  const pending = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (pending.current === null || !box.current) return;
    box.current.focus();
    box.current.setSelectionRange(pending.current, pending.current);
    pending.current = null;
  }, [value]);

  const at = mentionAt(value, caret);
  const choices = useMemo(() => {
    if (!at) return [];
    const q = at.query;
    const score = (p: Mentionable) => {
      const login = p.login.toLowerCase();
      const name = p.name.toLowerCase();
      if (login.startsWith(q)) return 0;
      if (name.split(/\s+/).some((w) => w.startsWith(q))) return 1;
      if (login.includes(q) || name.includes(q)) return 2;
      return 3;
    };
    return people
      .map((p) => ({ p, s: score(p) }))
      .filter((x) => x.s < 3)
      .sort((a, b) => a.s - b.s || a.p.login.localeCompare(b.p.login))
      .slice(0, MAX)
      .map((x) => x.p);
  }, [people, at?.query, at?.start]);
  const open = !closed && at !== null && choices.length > 0;

  const pick = (person: Mentionable) => {
    if (!at) return;
    const before = value.slice(0, at.start);
    const after = value.slice(caret).replace(/^[A-Za-z0-9._-]*/, "");
    const next = `${before}@${person.login} ${after.replace(/^ /, "")}`;
    const position = before.length + person.login.length + 2;
    // The caret goes after the name once it is in the box (the layout effect below).
    pending.current = position;
    onValue(next);
    setCaret(position);
  };

  const track = (el: HTMLTextAreaElement) => setCaret(el.selectionStart ?? 0);

  return (
    <div className="relative">
      <Textarea
        {...props}
        ref={box}
        value={value}
        onChange={(e) => {
          onValue(e.target.value);
          track(e.target);
          setActive(0);
          setClosed(false);
        }}
        onSelect={(e) => track(e.currentTarget)}
        onClick={(e) => track(e.currentTarget)}
        onBlur={() => setTimeout(() => setClosed(true), 150)}
        onFocus={() => setClosed(false)}
        onKeyDown={(e) => {
          if (open) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              const step = e.key === "ArrowDown" ? 1 : -1;
              setActive((i) => (i + step + choices.length) % choices.length);
              return;
            }
            if (e.key === "Enter" || e.key === "Tab") {
              e.preventDefault();
              pick(choices[Math.min(active, choices.length - 1)]);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setClosed(true);
              return;
            }
          }
          props.onKeyDown?.(e);
        }}
      />
      {open && (
        <div
          role="listbox"
          className={`absolute left-2 z-50 w-72 overflow-hidden rounded-md border bg-popover text-sm shadow-md ${above ? "bottom-full mb-1" : "top-full mt-1"}`}
        >
          {choices.map((p, i) => (
            <button
              key={p.login}
              type="button"
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(p);
              }}
              onMouseEnter={() => setActive(i)}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left ${i === active ? "bg-accent" : ""}`}
            >
              <PersonMark person={p} />
              <span className="font-semibold">{p.login}</span>
              <span className="truncate text-muted-foreground">{p.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function PersonMark({ person }: { person: Mentionable }) {
  return person.avatar ? (
    <img src={person.avatar} alt="" className="size-5 shrink-0 rounded-full" />
  ) : (
    <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground">
      {(person.name || person.login).slice(0, 1).toUpperCase()}
    </span>
  );
}
