// Syntax highlighting, as GitHub does it: Shiki's grammars (VS Code's) and
// GitHub's light and dark themes. One highlighter for the page; a language
// loads the first time a file in it is shown.
import { useEffect, useState } from "react";

import {
  bundledLanguagesInfo,
  createHighlighter,
  type Highlighter,
  type ThemedToken,
} from "shiki";

export type Line = ThemedToken[];

let highlighter: Promise<Highlighter> | null = null;
const load = () =>
  (highlighter ??= createHighlighter({
    themes: ["github-light", "github-dark"],
    langs: [],
  }));

const BY_NAME: Record<string, string> = {
  dockerfile: "docker",
  makefile: "make",
  gemfile: "ruby",
  rakefile: "ruby",
  jenkinsfile: "groovy",
};
const BY_EXTENSION: Record<string, string> = {
  yml: "yaml",
  h: "c",
  hh: "cpp",
  hpp: "cpp",
  cc: "cpp",
  mjs: "javascript",
  cjs: "javascript",
  mts: "typescript",
  cts: "typescript",
  pyi: "python",
  bash: "shellscript",
  sh: "shellscript",
  zsh: "shellscript",
};

function known(name: string): string | null {
  const lower = name.toLowerCase();
  const info = bundledLanguagesInfo.find(
    (l) => l.id === lower || l.aliases?.includes(lower),
  );
  return info?.id ?? null;
}

/** The language a file's name says it is in; ``null`` for plain text. */
export function languageOf(path: string): string | null {
  const name = path.split("/").pop() ?? "";
  if (BY_NAME[name.toLowerCase()]) return BY_NAME[name.toLowerCase()];
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return null;
  const ext = name.slice(dot + 1).toLowerCase();
  return BY_EXTENSION[ext] ?? known(ext);
}

/** The language a Markdown fence names (```py, ```typescript). */
export function fenceLanguage(name: string): string | null {
  return BY_EXTENSION[name.toLowerCase()] ?? known(name);
}

async function tokenize(code: string, lang: string): Promise<Line[]> {
  const h = await load();
  if (!h.getLoadedLanguages().includes(lang))
    await h.loadLanguage(lang as never);
  return h.codeToTokens(code, {
    lang: lang as never,
    themes: { light: "github-light", dark: "github-dark" },
  }).tokens;
}

/** ``code``'s lines as tokens, once they are ready (``null`` until then, or for plain text). */
export function useLines(code: string, lang: string | null): Line[] | null {
  const [lines, setLines] = useState<Line[] | null>(null);
  useEffect(() => {
    setLines(null);
    if (!lang || !code) return;
    let gone = false;
    tokenize(code, lang).then(
      (tokens) => !gone && setLines(tokens),
      () => !gone && setLines(null),
    );
    return () => {
      gone = true;
    };
  }, [code, lang]);
  return lines;
}

/** A line's tokens, colored for the light theme and (``.dark``) the dark one. */
export function Tokens({
  line,
  text,
}: {
  line: Line | undefined;
  text: string;
}) {
  if (!line) return <>{text}</>;
  return (
    <>
      {line.map((token, i) => (
        <span
          key={i}
          className="hl"
          style={
            (token.htmlStyle as React.CSSProperties) ?? { color: token.color }
          }
        >
          {token.content}
        </span>
      ))}
    </>
  );
}

/** A Markdown code block, highlighted when its fence names a language. */
export function CodeBlock({
  code,
  lang,
}: {
  code: string;
  lang: string | null;
}) {
  const lines = useLines(code, lang);
  if (!lines) return <code>{code}</code>;
  return (
    <code>
      {lines.map((line, i) => (
        <span key={i}>
          <Tokens line={line} text="" />
          {i < lines.length - 1 ? "\n" : ""}
        </span>
      ))}
    </code>
  );
}
