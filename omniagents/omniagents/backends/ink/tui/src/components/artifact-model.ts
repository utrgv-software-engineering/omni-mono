// Presentation logic for the artifacts drawer, kept out of the component
// so it is unit-testable without an Ink render (same split as
// ./model-picker-model and ./session-manager-model).
//
// The wire contract is `ui.add_artifact` with `{title, content, mode,
// artifact_id}` (omniagents/core/runtime/bridge.py `_send_artifact`),
// carrying `session_id` on the request envelope.
//
// The half that matters in a terminal: `image` / `pdf` / `docx` / `pptx`
// artifacts arrive as `data:<mime>;base64,<payload>` data URLs
// (omniagents/builtin/tools/artifacts.py). A megabyte of base64 printed
// into a TTY is not a degraded rendering, it is a wrecked session — so
// binary artifacts are never previewed here, only described and written
// to disk.

export type ArtifactEntry = {
  /** Server-assigned id; absent for one-off artifacts. */
  artifactId?: string;
  title: string;
  content: string;
  /** `markdown` | `html` | `image` | `pdf` (docx/pptx arrive as pdf). */
  mode: string;
  sessionId?: string;
  updatedAt: number;
  /** Bumped each time the same `artifactId` is re-emitted. */
  revision: number;
};

/** The decoded halves of a `data:<mime>;base64,<payload>` URL. */
export type DataUrlParts = { mime: string; base64: string };

const DATA_URL = /^data:([^;,]*);base64,([\s\S]*)$/;

/** Parse a base64 data URL, or null when `content` is not one. */
export function parseDataUrl(content: string): DataUrlParts | null {
  const match = DATA_URL.exec(content.trim());
  if (!match) return null;
  return {
    mime: match[1] || "application/octet-stream",
    base64: match[2] ?? "",
  };
}

/**
 * True when the artifact's payload is binary, and therefore must never
 * reach the terminal. Keyed off the payload (a data URL) rather than the
 * mode string alone: `mode` is server-declared and a text artifact
 * mislabelled `image` would otherwise get dumped as raw base64.
 */
export function isBinary(entry: ArtifactEntry): boolean {
  return parseDataUrl(entry.content) !== null;
}

/** Decoded byte length of a base64 payload, without decoding it. */
export function base64Bytes(base64: string): number {
  const clean = base64.replace(/\s/g, "");
  if (clean.length === 0) return 0;
  const padding = clean.endsWith("==") ? 2 : clean.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((clean.length * 3) / 4) - padding);
}

/** Payload size in bytes — decoded for binary, UTF-8 for text. */
export function artifactBytes(entry: ArtifactEntry): number {
  const parts = parseDataUrl(entry.content);
  if (parts) return base64Bytes(parts.base64);
  return Buffer.byteLength(entry.content, "utf8");
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The drawer's entries from the conversation's artifacts, which the shared
 * session already keeps one per id: re-emitting an id is "here is the new
 * version" (omniagents/core/conversation/recorder.py). Each entry keeps the
 * revision it had and bumps it when its content or title changed, which is
 * what lets the drawer show that it changed.
 */
export function reviseArtifacts(
  previous: readonly ArtifactEntry[],
  current: readonly Omit<ArtifactEntry, "revision">[],
): ArtifactEntry[] {
  return current.map((entry) => {
    const before = entry.artifactId
      ? previous.find(
          (existing) =>
            existing.artifactId === entry.artifactId &&
            existing.sessionId === entry.sessionId,
        )
      : undefined;
    if (!before) return { ...entry, revision: 0 };
    const changed =
      before.content !== entry.content || before.title !== entry.title;
    return { ...entry, revision: before.revision + (changed ? 1 : 0) };
  });
}

const MIME_EXTENSIONS: Record<string, string> = {
  "application/pdf": "pdf",
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/svg+xml": "svg",
};

const MODE_EXTENSIONS: Record<string, string> = {
  markdown: "md",
  html: "html",
  pdf: "pdf",
  image: "png",
};

/** File extension for an artifact, preferring the payload's own MIME. */
export function extensionFor(entry: ArtifactEntry): string {
  const parts = parseDataUrl(entry.content);
  if (parts) {
    const known = MIME_EXTENSIONS[parts.mime.toLowerCase()];
    if (known) return known;
    // `image/tiff` → `tiff`; anything unrecognized keeps a neutral suffix
    // rather than inventing one that a viewer would trust.
    const subtype = parts.mime.split("/")[1];
    return subtype && /^[a-z0-9.+-]+$/i.test(subtype)
      ? subtype.replace(/^x-/, "")
      : "bin";
  }
  return MODE_EXTENSIONS[entry.mode] ?? "txt";
}

/**
 * A filesystem-safe name derived from the title. Deliberately strips
 * path separators and leading dots: the title is agent-authored text,
 * and a "title" of `../../.bashrc` must not choose where the file lands.
 */
export function slugify(title: string): string {
  const slug = title
    .normalize("NFKD")
    .replace(/[^\w\s.-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .replace(/^[.-]+/, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 60);
  return slug || "artifact";
}

/** Default filename an artifact saves to, relative to the save directory. */
export function defaultFileName(entry: ArtifactEntry): string {
  return `${slugify(entry.title)}.${extensionFor(entry)}`;
}

/** The bytes to write when saving: decoded for binary, raw text otherwise. */
export function saveBuffer(entry: ArtifactEntry): Buffer {
  const parts = parseDataUrl(entry.content);
  if (parts) return Buffer.from(parts.base64, "base64");
  return Buffer.from(entry.content, "utf8");
}

/**
 * Wrap text for the preview pane, expanding tabs and dropping control
 * characters. A raw artifact can carry ANSI escapes or a stray `\r`;
 * printing those verbatim lets agent-authored content repaint the TUI's
 * chrome, so the preview is plain text by construction.
 */
export function previewLines(
  content: string,
  width: number,
  limit: number,
): string[] {
  // Honors the width it is given (guarding only against a non-positive
  // one). Choosing a sensible minimum for a narrow terminal is the
  // panel's call, not this function's — silently overriding an explicit
  // argument makes the wrapping untestable at the width that was asked
  // for.
  const safeWidth = width > 0 ? Math.floor(width) : 1;
  const lines: string[] = [];
  const source = content.replace(/\r\n?/g, "\n").split("\n");
  for (const raw of source) {
    const clean = raw
      .replace(/\t/g, "  ")
      // Control characters (ESC included) are stripped rather than
      // rendered: agent-authored content must not be able to repaint the
      // TUI's chrome through the preview pane.
      // eslint-disable-next-line no-control-regex
      .replace(/[\u0000-\u001f\u007f]/g, "");
    if (clean === "") {
      lines.push("");
    } else {
      for (let index = 0; index < clean.length; index += safeWidth) {
        lines.push(clean.slice(index, index + safeWidth));
      }
    }
    if (lines.length >= limit) return lines.slice(0, limit);
  }
  return lines;
}

/** One-line summary under the title: mode · size · revision. */
export function artifactMeta(entry: ArtifactEntry): string {
  const parts = [entry.mode, formatBytes(artifactBytes(entry))];
  if (entry.revision > 0) parts.push(`rev ${entry.revision + 1}`);
  if (isBinary(entry)) parts.push("binary");
  return parts.join(" · ");
}
