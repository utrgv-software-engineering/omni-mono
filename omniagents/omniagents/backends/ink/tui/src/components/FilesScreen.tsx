import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createHash } from "node:crypto";
import { Box, Text, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { FsListEntry, RPCClient } from "../rpc/client.js";
import { useActions } from "../store/appStore.js";
import { windowStart } from "./model-picker-model.js";
import { clampScroll } from "./git-model.js";
import { wrapPlain, wrapStyled } from "./agents-model.js";
import { highlight, supportsLanguage } from "cli-highlight";
import sliceAnsi from "slice-ansi";
import {
  baseName,
  buildTree,
  formatSize,
  languageForPath,
  sniffBinary,
  type TreeRow,
} from "./files-model.js";
import {
  applyEdit,
  fromText,
  toText,
  type EditorBuffer,
} from "./editor-model.js";
import { EmptyState } from "./shared/EmptyState.js";
import { Theme } from "../theme.js";

const SIDEBAR_BREAKPOINT = 90;
const TREE_WIDTH = 40;
const PAGE = 12;
// Preview reads at most this much — it's a peek, not an editor.
const PREVIEW_CAP = 128 * 1024;
// The inline editor loads whole files; bigger than this wants $EDITOR.
const EDIT_CAP = 512 * 1024;

type Preview = {
  path: string;
  size: number;
  binary: boolean;
  truncated: boolean;
  text: string;
};

type EditState = {
  path: string;
  /** sha256 of the downloaded original — the optimistic-concurrency
   * token for save (fs_upload_open expected_sha256). */
  sha: string;
  buffer: EditorBuffer;
  saving: boolean;
  notice: string | null;
};

type Props = {
  active: boolean;
  height: number;
  client: RPCClient | null;
  onExit: () => void;
  /** Drop `@path` into the chat composer and jump there. */
  onMention: (path: string) => void;
};

// The Files screen (tab 4): workspace browser, read-only. Lazy fs_list
// tree with ONE non-recursive fs_watch per expanded directory (the
// protocol's documented explorer scaling model), chunked fs_download
// preview, and the same spatial focus grammar as Review/Agents: ←/→
// switch panes, ↑↓ contextual, enter opens, esc pops.
export const FilesScreen = ({
  active,
  height,
  client,
  onExit,
  onMention,
}: Props) => {
  const { stdout } = useStdout();
  const terminalWidth = stdout?.columns ?? 80;
  const actions = useActions();

  const [entriesByDir, setEntriesByDir] = useState<
    Record<string, FsListEntry[] | undefined>
  >({});
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [cursor, setCursor] = useState(0);
  const [pane, setPane] = useState<"tree" | "preview">("tree");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewScroll, setPreviewScroll] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<EditState | null>(null);
  // First Esc on a dirty buffer arms the discard; the second discards.
  const [discardArm, setDiscardArm] = useState(false);
  // Async syntax colors for the editor: typing repaints plain
  // instantly, colors land after a short idle (highlighting a whole
  // medium file costs ~tens of ms — too slow for the keystroke path).
  const [editColored, setEditColored] = useState<{
    version: number;
    lines: string[];
  } | null>(null);
  const editVersionRef = useRef(0);
  // dir -> watch_id for every expanded directory (plus the root).
  const watchesRef = useRef<Record<string, string>>({});

  const environmentId = client?.defaultEnvironmentId() ?? null;
  const granted = !!client?.isGranted("fs_list");

  const loadDir = useCallback(
    async (dir: string) => {
      if (!client || !environmentId) return;
      try {
        const result = await client.fsList(environmentId, dir);
        setEntriesByDir((prev) => ({ ...prev, [dir]: result.entries }));
        setError(null);
      } catch (e) {
        setError((e as Error).message ?? String(e));
      }
    },
    [client, environmentId],
  );

  const watchDir = useCallback(
    async (dir: string) => {
      if (!client || !environmentId) return;
      if (watchesRef.current[dir]) return;
      try {
        const result = await client.fsWatch(environmentId, dir, {
          recursive: false,
        });
        watchesRef.current[dir] = result.watch_id;
      } catch {
        // Watching is a freshness nicety; browsing works without it.
      }
    },
    [client, environmentId],
  );

  const unwatchDir = useCallback(
    (dir: string) => {
      const watchId = watchesRef.current[dir];
      if (!watchId) return;
      delete watchesRef.current[dir];
      if (client && environmentId) {
        void client.fsUnwatch(environmentId, watchId).catch(() => {});
      }
    },
    [client, environmentId],
  );

  // Entry: reset and load the root. Exit: tear every watch down.
  useEffect(() => {
    if (!active) return;
    setCursor(0);
    setPane("tree");
    setPreview(null);
    setPreviewScroll(0);
    setEdit(null);
    setDiscardArm(false);
    setError(null);
    void loadDir(".");
    void watchDir(".");
    return () => {
      for (const dir of Object.keys(watchesRef.current)) {
        unwatchDir(dir);
      }
    };
    // Deps intentionally minimal: entry/exit lifecycle only.
  }, [active]);

  // Live refresh: a watched directory changed -> relist it. Overflow
  // (fs_rescan_required) uses the same recovery, which is exactly what
  // the protocol prescribes.
  useEffect(() => {
    if (!client || !active) return;
    const dirOf = (watchId: unknown) =>
      Object.entries(watchesRef.current).find(
        ([, id]) => id === watchId,
      )?.[0];
    const onEvents = (params: { watch_id?: string }) => {
      const dir = dirOf(params?.watch_id);
      if (dir !== undefined) void loadDir(dir);
    };
    const onRescan = (params: { watch_id?: string }) => {
      const dir = dirOf(params?.watch_id);
      if (dir !== undefined) void loadDir(dir);
    };
    client.on("fs_events", onEvents);
    client.on("fs_rescan_required", onRescan);
    return () => {
      client.off("fs_events", onEvents);
      client.off("fs_rescan_required", onRescan);
    };
  }, [client, active, loadDir]);

  const rows = useMemo(
    () => buildTree(entriesByDir, expanded),
    [entriesByDir, expanded],
  );

  useEffect(() => {
    setCursor((prev) =>
      rows.length === 0 ? 0 : Math.min(prev, rows.length - 1),
    );
  }, [rows.length]);

  const current = rows[cursor] ?? null;

  const toggleDir = useCallback(
    (row: TreeRow) => {
      if (row.expanded) {
        setExpanded((prev) => {
          const next = new Set(prev);
          next.delete(row.path);
          // Collapsing prunes descendants' watches too.
          for (const dir of Object.keys(watchesRef.current)) {
            if (dir === row.path || dir.startsWith(row.path + "/")) {
              unwatchDir(dir);
            }
          }
          for (const dir of [...next]) {
            if (dir.startsWith(row.path + "/")) next.delete(dir);
          }
          return next;
        });
      } else {
        setExpanded((prev) => new Set(prev).add(row.path));
        if (entriesByDir[row.path] === undefined) void loadDir(row.path);
        void watchDir(row.path);
      }
    },
    [entriesByDir, loadDir, watchDir, unwatchDir],
  );

  const downloadAll = useCallback(
    async (path: string, cap: number) => {
      if (!client || !environmentId) return null;
      const open = await client.fsDownloadOpen(environmentId, path);
      if (open.size > cap) {
        throw new Error(
          `${baseName(path)} is ${formatSize(open.size)} — too large (cap ${formatSize(cap)})`,
        );
      }
      let received = Buffer.alloc(0);
      let offset = 0;
      while (offset < open.size) {
        const chunk = await client.fsDownloadRead(
          environmentId,
          open.transfer_id,
          { offset },
        );
        received = Buffer.concat([
          received,
          Buffer.from(chunk.data, "base64"),
        ]);
        offset = chunk.offset + chunk.bytes;
        if (chunk.eof) break;
      }
      void client
        .fsDownloadClose(environmentId, open.transfer_id)
        .catch(() => {});
      return { bytes: received, sha: open.sha256, size: open.size };
    },
    [client, environmentId],
  );

  const startEdit = useCallback(
    async (path: string) => {
      if (busy || edit) return;
      setBusy(true);
      try {
        const result = await downloadAll(path, EDIT_CAP);
        if (!result) return;
        if (sniffBinary(result.bytes)) {
          setError(`${baseName(path)} is binary — not editable here`);
          return;
        }
        setEdit({
          path,
          sha: result.sha,
          buffer: fromText(result.bytes.toString("utf8")),
          saving: false,
          notice: null,
        });
        setDiscardArm(false);
        setPane("preview");
        setError(null);
      } catch (e) {
        setError((e as Error).message ?? String(e));
      } finally {
        setBusy(false);
      }
    },
    [busy, edit, downloadAll],
  );

  const saveEdit = useCallback(async () => {
    if (!client || !environmentId || !edit || edit.saving) return;
    setEdit((prev) => (prev ? { ...prev, saving: true, notice: null } : prev));
    try {
      const bytes = Buffer.from(toText(edit.buffer), "utf8");
      const sha = createHash("sha256").update(bytes).digest("hex");
      const open = await client.fsUploadOpen(
        environmentId,
        edit.path,
        bytes.length,
        { sha256: sha, expectedSha256: edit.sha, overwrite: true },
      );
      const chunkSize = open.chunk_size > 0 ? open.chunk_size : 64 * 1024;
      try {
        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
          await client.fsUploadChunk(
            environmentId,
            open.transfer_id,
            offset,
            bytes.subarray(offset, offset + chunkSize).toString("base64"),
          );
        }
        await client.fsUploadCommit(environmentId, open.transfer_id);
      } catch (e) {
        void client
          .fsUploadAbort(environmentId, open.transfer_id)
          .catch(() => {});
        throw e;
      }
      // Saved: the new content is the next edit's baseline.
      setEdit((prev) =>
        prev
          ? {
              ...prev,
              sha,
              saving: false,
              notice: "saved",
              buffer: { ...prev.buffer, dirty: false },
            }
          : prev,
      );
      setPreview(null); // stale — re-enter to re-read
    } catch (e) {
      const message = (e as Error).message ?? String(e);
      setEdit((prev) =>
        prev ? { ...prev, saving: false, notice: message } : prev,
      );
    }
  }, [client, environmentId, edit]);

  useEffect(() => {
    if (!edit) {
      setEditColored(null);
      return;
    }
    const language = languageForPath(edit.path);
    if (!language || !supportsLanguage(language)) {
      setEditColored(null);
      return;
    }
    const version = ++editVersionRef.current;
    const text = toText(edit.buffer);
    if (text.length > 200_000) {
      setEditColored(null);
      return;
    }
    const id = setTimeout(() => {
      try {
        setEditColored({
          version,
          lines: highlight(text, { language, ignoreIllegals: true }).split(
            "\n",
          ),
        });
      } catch {
        setEditColored(null);
      }
    }, 120);
    return () => clearTimeout(id);
  }, [edit?.buffer.lines, edit?.path, edit]);

  const openPreview = useCallback(
    async (row: TreeRow) => {
      if (!client || !environmentId || busy) return;
      setBusy(true);
      try {
        const open = await client.fsDownloadOpen(environmentId, row.path);
        let received = Buffer.alloc(0);
        let offset = 0;
        const limit = Math.min(open.size, PREVIEW_CAP);
        while (offset < limit) {
          const chunk = await client.fsDownloadRead(
            environmentId,
            open.transfer_id,
            { offset },
          );
          received = Buffer.concat([
            received,
            Buffer.from(chunk.data, "base64"),
          ]);
          offset = chunk.offset + chunk.bytes;
          if (chunk.eof) break;
        }
        void client
          .fsDownloadClose(environmentId, open.transfer_id)
          .catch(() => {});
        const binary = sniffBinary(received);
        setPreview({
          path: row.path,
          size: open.size,
          binary,
          truncated: !binary && received.length < open.size,
          text: binary ? "" : received.toString("utf8"),
        });
        setPreviewScroll(0);
        setPane("preview");
        setError(null);
      } catch (e) {
        setError((e as Error).message ?? String(e));
      } finally {
        setBusy(false);
      }
    },
    [client, environmentId, busy],
  );

  // ----- layout math (needed by the keys) -----

  const wide = terminalWidth >= SIDEBAR_BREAKPOINT;
  const bodyHeight = Math.max(4, height - 1);
  const treeRows = Math.max(3, bodyHeight - 4); // border + title + overflow
  const previewWidth = wide
    ? Math.max(20, terminalWidth - TREE_WIDTH - 2)
    : Math.max(20, terminalWidth - 1);
  // Wide mode spends one top row aligning with the sidebar's title row,
  // one bottom row on the range counter.
  const previewWindow = Math.max(3, bodyHeight - 2);

  const previewLines = useMemo(() => {
    if (!preview) return [];
    type Row = {
      text: string;
      color: string;
      bold?: boolean;
      /** Right-aligned line number; null = continuation/header row. */
      gutter?: string | null;
    };
    const lines: Row[] = [];
    for (const row of wrapPlain(preview.path, previewWidth)) {
      lines.push({ text: row, color: Theme.textPrimary, bold: true });
    }
    const meta = [formatSize(preview.size)];
    if (preview.binary) meta.push("binary");
    if (preview.truncated) meta.push(`first ${formatSize(PREVIEW_CAP)} shown`);
    lines.push({ text: meta.join(" · "), color: Theme.textChrome });
    lines.push({ text: "", color: Theme.textChrome });
    if (preview.binary) {
      lines.push({
        text: "(binary — no preview)",
        color: Theme.textChrome,
      });
      return lines;
    }
    const sourceLines = preview.text.split("\n");
    const gutterWidth = Math.max(3, String(sourceLines.length).length);
    const contentWidth = Math.max(20, previewWidth - gutterWidth - 1);
    // Syntax highlighting: highlight.js → ANSI, wrapped by VISUAL
    // width per source line (escape codes carry none). The first
    // wrapped row carries the number; continuations get blank gutter.
    const language = languageForPath(preview.path);
    let coloredLines: string[] | null = null;
    if (
      language &&
      preview.text.length < 200_000 &&
      supportsLanguage(language)
    ) {
      try {
        coloredLines = highlight(preview.text, {
          language,
          ignoreIllegals: true,
        }).split("\n");
        if (coloredLines.length !== sourceLines.length) coloredLines = null;
      } catch {
        coloredLines = null;
      }
    }
    sourceLines.forEach((raw, index) => {
      const source = coloredLines ? coloredLines[index]! : raw;
      const wrapped = coloredLines
        ? wrapStyled(source, contentWidth)
        : wrapPlain(source, contentWidth);
      wrapped.forEach((row, wrapIndex) => {
        lines.push({
          text: row,
          color: Theme.textSecondary,
          gutter:
            wrapIndex === 0
              ? String(index + 1).padStart(gutterWidth)
              : " ".repeat(gutterWidth),
        });
      });
    });
    return lines;
  }, [preview, previewWidth]);

  // Publish the focus path to the status bar.
  useEffect(() => {
    if (!active) return;
    actions.setStatusContext(
      edit
        ? `files · edit · ${baseName(edit.path)}${edit.buffer.dirty ? " *" : ""}`
        : pane === "preview" && preview
          ? `files · ${baseName(preview.path)}`
          : "files",
    );
    return () => actions.setStatusContext(null);
  }, [active, pane, preview, edit, actions]);

  const previewFocused = pane === "preview" && preview !== null;

  const editRows = Math.max(3, bodyHeight - 3); // header + notice + hint

  useKeypress(
    (input, key) => {
      if (!edit) return false;
      if (key.tab && key.ctrl) return false;
      if (key.ctrl && input === "s") {
        void saveEdit();
        return true;
      }
      if (key.escape || (key.meta && key.escape)) {
        if (edit.buffer.dirty && !discardArm) {
          setDiscardArm(true);
          return true;
        }
        setEdit(null);
        setDiscardArm(false);
        return true;
      }
      if (key.ctrl || (key.meta && input.length === 0)) return true;
      setDiscardArm(false);
      setEdit((prev) =>
        prev
          ? {
              ...prev,
              notice: null,
              // Ink names the 0x7f byte — what Backspace actually
              // sends — "delete" (and the forward-Delete CSI too, so
              // the two are indistinguishable here). Users pressing
              // that key mean backward-delete.
              buffer: applyEdit(
                prev.buffer,
                input,
                { ...key, backspace: key.backspace || key.delete, delete: false },
                editRows,
              ),
            }
          : prev,
      );
      return true; // the editor owns the keyboard, tab included
    },
    {
      isActive: active && edit !== null,
      priority: KeypressPriority.Critical,
    },
  );

  useKeypress(
    (input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "q") {
        // Esc pops one level: preview → tree → chat.
        if (previewFocused) {
          setPane("tree");
        } else {
          onExit();
        }
        return true;
      }
      if (input === "r") {
        for (const dir of Object.keys(watchesRef.current)) {
          void loadDir(dir);
        }
        return true;
      }
      // ←/→ (h/l) move focus between the tree and the preview.
      if (key.leftArrow || input === "h") {
        setPane("tree");
        return true;
      }
      if ((key.rightArrow || input === "l") && preview) {
        setPane("preview");
        return true;
      }
      // Enter: descend into a directory, open a file's preview.
      if (key.return && current && pane === "tree") {
        if (current.type === "directory") {
          toggleDir(current);
        } else {
          void openPreview(current);
        }
        return true;
      }
      // ↑↓/j/k: tree cursor when the tree owns focus, preview scrolling
      // when the preview does.
      if (key.downArrow || input === "j") {
        if (previewFocused) {
          setPreviewScroll((prev) =>
            clampScroll(prev + 1, previewLines.length, previewWindow),
          );
        } else if (rows.length > 0) {
          setCursor((prev) => Math.min(rows.length - 1, prev + 1));
        }
        return true;
      }
      if (key.upArrow || input === "k") {
        if (previewFocused) {
          setPreviewScroll((prev) =>
            clampScroll(prev - 1, previewLines.length, previewWindow),
          );
        } else if (rows.length > 0) {
          setCursor((prev) => Math.max(0, prev - 1));
        }
        return true;
      }
      if (key.pageDown || input === "J") {
        setPreviewScroll((prev) =>
          clampScroll(prev + PAGE, previewLines.length, previewWindow),
        );
        return true;
      }
      if (key.pageUp || input === "K") {
        setPreviewScroll((prev) =>
          clampScroll(prev - PAGE, previewLines.length, previewWindow),
        );
        return true;
      }
      // e edits the file inline (save = upload with sha conflict check).
      if (input === "e") {
        const target =
          previewFocused && preview
            ? preview.path
            : current?.type === "file"
              ? current.path
              : null;
        if (target) void startEdit(target);
        return true;
      }
      // m mentions the file in chat: `@path` lands in the composer.
      if (input === "m") {
        const target =
          previewFocused && preview ? preview.path : current?.path;
        if (target) onMention(target);
        return true;
      }
      return false;
    },
    { isActive: active && edit === null, priority: KeypressPriority.High },
  );

  if (!active) return null;

  if (!granted) {
    return (
      <Box flexDirection="column" height={height} width="100%" flexGrow={1}>
        <EmptyState
          title="files unavailable"
          lines={[
            "this server did not grant the experimental fs_* operations",
          ]}
          actions={[{ keys: "esc", label: "back to chat" }]}
        />
        <Text color={Theme.textChrome}>esc chat</Text>
      </Box>
    );
  }

  const listStart = windowStart(cursor, rows.length, treeRows);
  const visibleRows = rows.slice(listStart, listStart + treeRows);
  const visiblePreview = previewLines.slice(
    previewScroll,
    previewScroll + previewWindow,
  );

  const tree = (
    <Box
      flexDirection="column"
      width={wide ? TREE_WIDTH : undefined}
      flexShrink={0}
      height={bodyHeight}
      paddingX={1}
      borderStyle="round"
      borderColor={pane === "tree" ? Theme.borderFocus : Theme.borderAmbient}
    >
      <Text color={Theme.textChrome}>
        files{" "}
        {busy && (
          <Text color={Theme.stateActive}>
            <Spinner type="dots" />
          </Text>
        )}
      </Text>
      {rows.length === 0 ? (
        <Text color={Theme.textChrome}>
          {error ?? "(empty directory)"}
        </Text>
      ) : (
        visibleRows.map((row, i) => {
          const index = listStart + i;
          const isCurrent = index === cursor;
          const glyph =
            row.type === "directory" ? (row.expanded ? "▾ " : "▸ ") : "  ";
          return (
            <Box key={row.path}>
              <Text color={Theme.focus}>{isCurrent ? "▶" : " "}</Text>
              <Text color={Theme.textChrome}>
                {"  ".repeat(row.depth)}
              </Text>
              <Text
                color={
                  row.type === "directory"
                    ? Theme.stateActive
                    : Theme.textChrome
                }
              >
                {glyph}
              </Text>
              {/* minWidth 0 — a long name must truncate, never wrap
                  into a phantom second line (see ReviewScreen sidebar).
                  The row deliberately carries no trailing column: a size
                  here got squeezed by the deeper indents and wrapped at
                  its own space ("12.3" / "KB"), splitting the row. */}
              <Box flexGrow={1} minWidth={0}>
                <Text
                  color={isCurrent ? Theme.textPrimary : Theme.textSecondary}
                  bold={isCurrent}
                  wrap="truncate-end"
                >
                  {row.name}
                </Text>
              </Box>
            </Box>
          );
        })
      )}
      {rows.length > treeRows && (
        <Text color={Theme.textChrome}>
          {listStart + 1}–{Math.min(listStart + treeRows, rows.length)} of{" "}
          {rows.length}
        </Text>
      )}
    </Box>
  );

  const previewPane = (
    <Box
      flexDirection="column"
      flexGrow={1}
      height={bodyHeight}
      paddingLeft={wide ? 1 : 0}
      paddingTop={1}
    >
      {edit !== null ? (
        <>
          <Box>
            <Text color={Theme.textPrimary} bold>
              {edit.path}
            </Text>
            {edit.buffer.dirty && <Text color={Theme.stateBlocked}> *</Text>}
            {edit.saving && (
              <Text color={Theme.stateActive}>
                {" "}
                <Spinner type="dots" /> saving
              </Text>
            )}
          </Box>
          {(edit.notice || discardArm) && (
            <Text
              color={
                discardArm || edit.notice !== "saved"
                  ? Theme.stateBlocked
                  : Theme.stateDone
              }
            >
              {discardArm
                ? "unsaved changes — esc again to discard"
                : edit.notice}
            </Text>
          )}
          {edit.buffer.lines
            .slice(edit.buffer.top, edit.buffer.top + editRows)
            .map((line, i) => {
              const row = edit.buffer.top + i;
              const gutterWidth = Math.max(
                3,
                String(edit.buffer.lines.length).length,
              );
              const gutter = String(row + 1).padStart(gutterWidth);
              // Colors apply only when they match the CURRENT text —
              // a stale async result must never desync with the cursor.
              const colored =
                editColored &&
                editColored.version === editVersionRef.current &&
                editColored.lines.length === edit.buffer.lines.length
                  ? editColored.lines[row]
                  : null;
              if (row !== edit.buffer.row) {
                const shown = colored ?? line;
                return (
                  <Box key={row}>
                    <Text color={Theme.textChrome}>{gutter} </Text>
                    <Box flexGrow={1} minWidth={0}>
                      <Text color={Theme.textSecondary} wrap="truncate-end">
                        {shown.length > 0 ? shown : " "}
                      </Text>
                    </Box>
                  </Box>
                );
              }
              const col = edit.buffer.col;
              const at = line.slice(col, col + 1) || " ";
              const pre =
                colored !== null
                  ? sliceAnsi(colored, 0, col)
                  : line.slice(0, col);
              const post =
                colored !== null
                  ? sliceAnsi(colored, col + 1)
                  : line.slice(col + 1);
              return (
                <Box key={row}>
                  <Text color={Theme.focus}>{gutter} </Text>
                  <Box flexGrow={1} minWidth={0}>
                    <Text
                      color={Theme.textPrimary}
                      wrap="truncate-end"
                    >
                      {pre}
                      <Text inverse>{at}</Text>
                      {post}
                    </Text>
                  </Box>
                </Box>
              );
            })}
        </>
      ) : preview === null ? (
        <Text color={Theme.textChrome}>
          {error ?? "enter on a file previews it here"}
        </Text>
      ) : (
        <>
          {visiblePreview.map((line, i) => (
            <Box key={previewScroll + i}>
              {"gutter" in line && line.gutter != null && (
                <Text color={Theme.textChrome}>{line.gutter} </Text>
              )}
              <Box flexGrow={1} minWidth={0}>
                <Text
                  color={line.color}
                  bold={line.bold}
                  wrap="truncate-end"
                >
                  {line.text.length > 0 ? line.text : " "}
                </Text>
              </Box>
            </Box>
          ))}
          {previewLines.length > previewWindow && (
            <Text color={Theme.textChrome}>
              {previewScroll + 1}–
              {Math.min(previewScroll + previewWindow, previewLines.length)}{" "}
              of {previewLines.length}
            </Text>
          )}
        </>
      )}
    </Box>
  );

  return (
    <Box flexDirection="column" height={height} width="100%" flexGrow={1}>
      {wide ? (
        <Box flexDirection="row" flexGrow={1}>
          {tree}
          {previewPane}
        </Box>
      ) : pane === "preview" && preview ? (
        previewPane
      ) : (
        tree
      )}

      <Text color={Theme.textChrome}>
        {edit !== null
          ? "editing · ctrl+s save · esc discard · tab indent"
          : previewFocused
            ? "↑↓ scroll · ←/esc tree · e edit · m mention"
            : "↑↓ move · enter open · e edit · m mention · r refresh · esc chat"}
      </Text>
    </Box>
  );
};
