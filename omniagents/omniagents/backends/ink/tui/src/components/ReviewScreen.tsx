import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Text, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { GitDiffFile, GitRepository, RPCClient } from "../rpc/client.js";
import { useActions, useRunDiff } from "../store/appStore.js";
import {
  buildReviewView,
  fileBadge,
  numberedFromHunks,
  numberUnified,
  splitUnifiedDiff,
  type ReviewFile,
} from "./review-model.js";
import {
  buildFileRows,
  clampScroll,
  confirmationOf,
  flattenDiff,
  type GitFileRow,
} from "./git-model.js";
import { EmptyState } from "./shared/EmptyState.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Theme } from "../theme.js";

const PAGE = 12;
// The sidebar is a synced table of contents for the unified diff —
// lazygit-style master/detail. It shows when the terminal is wide
// enough to afford it.
const SIDEBAR_BREAKPOINT = 90;
// 40 is the width every TUI file sidebar settles on — neo-tree's
// `window.width`, snacks' `M.sidebar`, and this TUI's own Files tree.
// It was 30, which left the path column ~26 columns after the border,
// the padding and the change badge.
const SIDEBAR_WIDTH = 40;

type Props = {
  active: boolean;
  height: number;
  client: RPCClient | null;
  onExit: () => void;
};

type PendingConfirmation = {
  label: string;
  message: string;
  token: string;
  paths: string[];
  run: (token: string) => Promise<unknown>;
};

// The Review screen (tab 2): two scopes, one key, no filter system.
// It opens on the last turn's run diff — "what did the agent just
// change" is the question you arrive with — falling back to the
// working tree when the turn changed nothing. `t` flips between them:
// the turn scope is a read-only record (push-updated at run end); the
// working scope is everything uncommitted, staging state as a per-row
// badge (git's XY code), space toggling it, x discarding, c committing.
// Both render ONE scrollable unified numbered diff beside the list.
export const ReviewScreen = ({ active, height, client, onExit }: Props) => {
  const { stdout } = useStdout();
  const terminalWidth = stdout?.columns ?? 80;
  const actions = useActions();
  const runDiff = useRunDiff();

  const [repos, setRepos] = useState<GitRepository[]>([]);
  const [repoIndex, setRepoIndex] = useState(0);
  const [gitFiles, setGitFiles] = useState<GitDiffFile[]>([]);
  const [workingRows, setWorkingRows] = useState<GitFileRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scroll, setScroll] = useState(0);
  const [fileIndex, setFileIndex] = useState(0);
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [commitMode, setCommitMode] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [confirm, setConfirm] = useState<PendingConfirmation | null>(null);
  // Which pane owns ↑↓: the file list or the diff. ←/→ (and h/l)
  // switch; enter on a file focuses the diff, esc pops back —
  // lazygit's spatial model. Only meaningful when the sidebar shows.
  const [pane, setPane] = useState<"files" | "diff">("files");
  const [scope, setScope] = useState<"working" | "turn">("turn");
  const windowRef = useRef(10);

  const environmentId = client?.defaultEnvironmentId() ?? null;
  const gitGranted = !!client?.isGranted("git_diff");
  const repo = repos[repoIndex]?.repo ?? null;

  // ----- data: git_status for badges, head diff for hunks -----

  const load = useCallback(async () => {
    if (!client || !environmentId || !gitGranted) return;
    setLoading(true);
    setError(null);
    try {
      let repoList = repos;
      if (repoList.length === 0) {
        const listed = await client.gitListRepositories(environmentId);
        repoList = listed.repositories;
        setRepos(repoList);
      }
      const target = repoList[repoIndex]?.repo ?? repoList[0]?.repo;
      if (target === undefined) {
        setWorkingRows([]);
        setGitFiles([]);
        setError("no git repositories in this environment");
        return;
      }
      const status = await client.gitStatus(environmentId, target, {
        includeUntracked: true,
      });
      setWorkingRows(buildFileRows(status));
      const result = await client.gitDiff(environmentId, target, {
        mode: "head",
        includeUntracked: true,
      });
      setGitFiles(result.files);
    } catch (e) {
      setError((e as Error).message ?? String(e));
      setWorkingRows([]);
      setGitFiles([]);
    } finally {
      setLoading(false);
    }
  }, [client, environmentId, gitGranted, repoIndex, repos]);

  useEffect(() => {
    if (!active) {
      setCommitMode(false);
      setCommitMessage("");
      setConfirm(null);
      return;
    }
    setScroll(0);
    setFileIndex(0);
    setPane("files");
    // Open on the turn diff when there is one; a chat-only turn (or a
    // fresh session) falls back to the working tree instead of showing
    // an empty screen. Deliberately reads runDiff without depending on
    // it — the scope is chosen on entry, not yanked mid-view.
    setScope(runDiff && runDiff.files.length > 0 ? "turn" : "working");
    setError(null);
    void load();
    // Deps intentionally omit load: fetch on entry/repo change; 'r'
    // refreshes after that.
  }, [active, repoIndex]);

  // ----- projections -----

  const workingFiles: ReviewFile[] = useMemo(
    () =>
      workingRows.map((row) => {
        const diffFile = gitFiles.find((f) => f.path === row.path);
        return {
          path: row.path,
          change: row.untracked ? "added" : "modified",
          additions: diffFile?.added_lines ?? null,
          deletions: diffFile?.deleted_lines ?? null,
          xy: row.xy,
          staged: row.staged,
          unstaged: row.unstaged,
          untracked: row.untracked,
          conflicted: row.conflicted,
        };
      }),
    [workingRows, gitFiles],
  );

  const turnFiles: ReviewFile[] = useMemo(
    () =>
      (runDiff?.files ?? []).map((file) => ({
        path: file.path,
        change: file.change_type,
        additions: file.additions,
        deletions: file.deletions,
      })),
    [runDiff],
  );

  const turnRaw = useMemo(
    () => splitUnifiedDiff(runDiff?.diff ?? ""),
    [runDiff],
  );

  const files = scope === "working" ? workingFiles : turnFiles;

  const view = useMemo(() => {
    if (scope === "turn") {
      return buildReviewView(turnFiles, (index) => {
        const entry = turnFiles[index]!;
        const raw = turnRaw.get(entry.path);
        if (!raw) {
          return [
            {
              text: "(content not carried — binary, oversize, or truncated)",
              color: Theme.textChrome,
            },
          ];
        }
        return numberUnified(raw);
      });
    }
    return buildReviewView(workingFiles, (index) => {
      const entry = workingFiles[index]!;
      const file = gitFiles.find((f) => f.path === entry.path);
      if (!file) {
        // With include_untracked the server synthesizes added-file
        // hunks, so this is only reachable when the diff was truncated
        // or the file vanished between status and diff.
        return [
          {
            text: "(content unavailable)",
            color: Theme.textChrome,
          },
        ];
      }
      // Binary/submodule files have no hunks — flattenDiff reports them.
      if (file.binary || file.submodule) return flattenDiff(file);
      return numberedFromHunks(file.hunks);
    });
  }, [scope, workingFiles, turnFiles, turnRaw, gitFiles]);

  useEffect(() => {
    setFileIndex((prev) =>
      files.length === 0 ? 0 : Math.min(prev, files.length - 1),
    );
  }, [files.length]);
  const current = view.sections[fileIndex] ?? null;

  // Publish the focus path to the status bar: the screen while the
  // file list owns focus, the file being read once the diff does.
  useEffect(() => {
    if (!active) return;
    const parts = ["review"];
    if (scope === "turn") parts.push("turn");
    if (scope === "working" && repos.length > 1 && repo) {
      const entry = repos[repoIndex];
      parts.push(
        repo === "."
          ? (entry?.absolute_root.split("/").filter(Boolean).pop() ?? repo)
          : repo,
      );
    }
    if (terminalWidth >= SIDEBAR_BREAKPOINT && pane === "diff") {
      const name = files[fileIndex]?.path.split("/").pop();
      if (name) parts.push(name);
    }
    if (loading) parts.push("loading…");
    actions.setStatusContext(parts.join(" · "));
    return () => actions.setStatusContext(null);
  }, [
    active,
    repo,
    repos,
    repoIndex,
    loading,
    pane,
    scope,
    fileIndex,
    files,
    terminalWidth,
    actions,
  ]);

  const moveCursor = useCallback(
    (delta: number) => {
      setFileIndex((prev) => {
        const next = Math.max(0, Math.min(files.length - 1, prev + delta));
        const offset = view.sections[next]?.offset ?? 0;
        setScroll(clampScroll(offset, view.lines.length, windowRef.current));
        return next;
      });
    },
    [files.length, view],
  );

  // ----- ops (stage/unstage/discard/commit with -32093 confirm) -----

  const runOp = useCallback(
    async (label: string, op: (token?: string) => Promise<unknown>) => {
      setBusy(true);
      setStatusLine(`${label}…`);
      try {
        await op();
        setStatusLine(`${label}: done`);
        void load();
      } catch (e) {
        const confirmation = confirmationOf(e);
        if (confirmation !== null) {
          setConfirm({
            label,
            message: (e as Error).message,
            token: confirmation.token,
            paths: confirmation.paths,
            run: op,
          });
          setStatusLine(null);
          return;
        }
        setStatusLine(`${label} failed: ${(e as Error).message ?? String(e)}`);
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  useEffect(() => {
    if (!statusLine || busy) return;
    const id = setTimeout(() => setStatusLine(null), 4000);
    return () => clearTimeout(id);
  }, [statusLine, busy]);

  // ----- layout math (needed by the keys) -----

  const showSidebarLayout = terminalWidth >= SIDEBAR_BREAKPOINT;
  // Both modes spend one extra row above the diff: the narrow mode's
  // active-file row, or the wide mode's top padding that aligns the
  // diff's first line with the sidebar's title row (the border owns
  // row 0).
  const chromeRows =
    2 +
    (statusLine ? 1 : 0) +
    (confirm ? 2 : 0) +
    (commitMode ? 1 : 0);
  const window = Math.max(4, height - chromeRows);
  windowRef.current = window;

  // ----- keyboard -----

  useKeypress(
    (input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      const diffFocused = showSidebarLayout && pane === "diff";
      if (key.escape || input === "q") {
        // Esc pops one level: diff pane → file list → chat.
        if (diffFocused) {
          setPane("files");
        } else {
          onExit();
        }
        return true;
      }
      if (input === "r") {
        void load();
        return true;
      }
      if (input === "]" && repos.length > 1) {
        setRepoIndex((prev) => (prev + 1) % repos.length);
        return true;
      }
      // t flips the scope: working tree ↔ the last turn's run diff.
      if (input === "t") {
        setScope((prev) => (prev === "working" ? "turn" : "working"));
        setScroll(0);
        setFileIndex(0);
        return true;
      }
      // ←/→ (h/l) move focus between the file list and the diff.
      if (showSidebarLayout && (key.leftArrow || input === "h")) {
        setPane("files");
        return true;
      }
      if (showSidebarLayout && (key.rightArrow || input === "l")) {
        setPane("diff");
        return true;
      }
      // Enter on a file opens (focuses) its diff.
      if (key.return && showSidebarLayout && pane === "files") {
        setPane("diff");
        return true;
      }
      // ↑↓/j/k: file cursor when the list owns focus (or in narrow
      // mode), diff scrolling when the diff owns it.
      if (key.downArrow || input === "j") {
        if (diffFocused) {
          setScroll((prev) =>
            clampScroll(prev + 1, view.lines.length, window),
          );
        } else if (files.length > 0) {
          moveCursor(1);
        }
        return true;
      }
      if (key.upArrow || input === "k") {
        if (diffFocused) {
          setScroll((prev) =>
            clampScroll(prev - 1, view.lines.length, window),
          );
        } else if (files.length > 0) {
          moveCursor(-1);
        }
        return true;
      }
      // PgUp/PgDn (and J/K) always page the diff.
      if (key.pageDown || input === "J") {
        setScroll((prev) =>
          clampScroll(prev + PAGE, view.lines.length, window),
        );
        return true;
      }
      if (key.pageUp || input === "K") {
        setScroll((prev) =>
          clampScroll(prev - PAGE, view.lines.length, window),
        );
        return true;
      }
      const selected = files[fileIndex];
      // Staging verbs only exist on the working scope — the turn diff
      // is a read-only record of what the agent changed.
      if (scope === "working" && client && environmentId && repo && selected) {
        const path = selected.path;
        // Space toggles the selected file's index state — fully staged
        // files unstage, everything else stages. Per-row, lazygit-style.
        if (input === " ") {
          if (selected.staged && !selected.unstaged) {
            void runOp(`unstage ${path}`, () =>
              client.gitUnstage(environmentId, repo, { paths: [path] }),
            );
          } else {
            void runOp(`stage ${path}`, () =>
              client.gitStage(environmentId, repo, { paths: [path] }),
            );
          }
          return true;
        }
        if (input === "x") {
          void runOp(`discard ${path}`, (token) =>
            client.gitDiscard(environmentId, repo, {
              paths: [path],
              confirmationToken: token,
            }),
          );
          return true;
        }
        if (input === "c") {
          setCommitMode(true);
          return true;
        }
      }
      return false;
    },
    {
      isActive: active && !commitMode && !confirm,
      priority: KeypressPriority.High,
    },
  );

  // Commit-message input.
  useKeypress(
    (input, key) => {
      if (key.escape) {
        setCommitMode(false);
        setCommitMessage("");
        return true;
      }
      if (key.return) {
        const message = commitMessage.trim();
        if (!message) {
          setStatusLine("commit message is empty");
          return true;
        }
        setCommitMode(false);
        setCommitMessage("");
        if (client && environmentId && repo) {
          void runOp("commit", (token) =>
            client.gitCommit(environmentId, repo, message, {
              confirmationToken: token,
            }),
          );
        }
        return true;
      }
      if (key.ctrl || key.meta) return false;
      if (key.tab) return true;
      if (key.backspace || key.delete) {
        setCommitMessage((prev) => prev.slice(0, -1));
        return true;
      }
      if (input) {
        setCommitMessage((prev) => prev + input);
        return true;
      }
      return true;
    },
    { isActive: active && commitMode, priority: KeypressPriority.Critical },
  );

  // Destructive-op confirmation.
  useKeypress(
    (input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false;
      if (!confirm) return true;
      if (input === "y") {
        const pending = confirm;
        setConfirm(null);
        void runOp(pending.label, () => pending.run(pending.token));
        return true;
      }
      if (input === "n" || key.escape) {
        setConfirm(null);
        setStatusLine(`${confirm.label}: cancelled`);
        return true;
      }
      return true;
    },
    {
      isActive: active && confirm !== null,
      priority: KeypressPriority.Critical,
    },
  );

  if (!active) return null;

  const empty = view.lines.length === 0 && !loading;
  const visible = view.lines.slice(scroll, scroll + window);

  // ----- table-of-contents sidebar (synced to the cursor) -----

  const showSidebar = showSidebarLayout && files.length > 0;
  const sidebarRows = Math.max(1, window - 3); // border + title row
  const sidebarStart = Math.max(
    0,
    Math.min(
      Math.max(0, fileIndex - Math.floor(sidebarRows / 2)),
      Math.max(0, files.length - sidebarRows),
    ),
  );
  const sidebarFiles = files.slice(sidebarStart, sidebarStart + sidebarRows);

  const sidebar = (
    <Box
      flexDirection="column"
      width={SIDEBAR_WIDTH}
      flexShrink={0}
      height={window + 1}
      paddingX={1}
      borderStyle="round"
      borderColor={pane === "files" ? Theme.borderFocus : Theme.borderAmbient}
    >
      <Text color={Theme.textChrome}>
        files <Text color={Theme.stateActive}>{files.length}</Text>
      </Text>
      {sidebarFiles.map((file, i) => {
        const index = sidebarStart + i;
        const isCurrent = index === fileIndex;
        const badge = fileBadge(file);
        const hasStats = file.additions !== null || file.deletions !== null;
        return (
          <SelectableRow
            key={file.path}
            selected={isCurrent}
          >
            <RowText color={badge.color}>{badge.text} </RowText>
            {/* minWidth 0: without it the truncating text's intrinsic
                width is a floor, an overflowing row wraps to height 2,
                and the cursor gets squeezed into a phantom blank line. */}
            <Box flexGrow={1} minWidth={0}>
              <RowText
                color={isCurrent ? Theme.textPrimary : Theme.textChrome}
                wrap="truncate-start"
              >
                {file.path}
              </RowText>
            </Box>
            {/* flexShrink 0: the counts are short and fixed-width, so
                the path column (minWidth 0, truncating) absorbs the
                shrink instead. Without it Yoga squeezes this column
                below its intrinsic width and Ink wraps it at the space
                between the two counts, splitting the row into two. */}
            {hasStats && (
              <Box flexShrink={0}>
                <RowText>
                  {file.additions !== null && (
                    <RowText color={Theme.stateDone}> +{file.additions}</RowText>
                  )}
                  {file.deletions !== null && (
                    <RowText color={Theme.stateFailed}> −{file.deletions}</RowText>
                  )}
                </RowText>
              </Box>
            )}
          </SelectableRow>
        );
      })}
      {files.length > sidebarRows && (
        <Text color={Theme.textChrome}>
          {sidebarStart + 1}–
          {Math.min(sidebarStart + sidebarRows, files.length)} of{" "}
          {files.length}
        </Text>
      )}
    </Box>
  );

  return (
    <Box flexDirection="column" height={height} width="100%" flexGrow={1}>
      {/* Narrow mode: the active file gets its own row (the sidebar
          shows it at wider sizes). */}
      {!showSidebarLayout && current && !empty && (
        <Box>
          <Text
            color={
              files[fileIndex]
                ? fileBadge(files[fileIndex]!).color
                : Theme.stateActive
            }
          >
            ▌ {files[fileIndex] ? fileBadge(files[fileIndex]!).text : "M "}{" "}
          </Text>
          <Box flexGrow={1} minWidth={0}>
            <Text color={Theme.textPrimary} wrap="truncate-start">
              {current.path}
            </Text>
          </Box>
          <Text color={Theme.textChrome}>
            {" "}
            ({fileIndex + 1}/{view.sections.length})
          </Text>
        </Box>
      )}

      {empty ? (
        scope === "turn" ? (
          <EmptyState
            title="no changes this turn"
            lines={["when the agent edits files, its run diff lands here"]}
            actions={[
              { keys: "t", label: "back to the working tree" },
              { keys: "esc", label: "back to chat" },
            ]}
          />
        ) : (
          <EmptyState
            title={error ?? "working tree clean"}
            lines={[
              "everything is committed — changes land here as they happen",
            ]}
            actions={[
              { keys: "↑↓", label: "select a file" },
              { keys: "space", label: "stage / unstage it" },
              { keys: "t", label: "see the last turn's changes" },
              { keys: "esc", label: "back to chat" },
            ]}
          />
        )
      ) : (
        <Box flexDirection="row" flexGrow={1}>
          {showSidebar && sidebar}
          <Box
            flexDirection="column"
            flexGrow={1}
            paddingLeft={showSidebar ? 1 : 0}
            paddingTop={showSidebar ? 1 : 0}
          >
            {visible.map((line, i) => (
              <Text
                key={scroll + i}
                color={line.color}
                bold={line.bold}
                wrap="truncate-end"
              >
                {/* Blank separator rows must occupy their row — empty
                    Text collapses to zero height in Ink. */}
                {line.text.length > 0 ? line.text : " "}
              </Text>
            ))}
          </Box>
        </Box>
      )}

      {statusLine && (
        <Box>
          {busy && (
            <Text color={Theme.stateActive}>
              <Spinner type="dots" />{" "}
            </Text>
          )}
          <Text color={Theme.stateBlocked} italic>
            {statusLine}
          </Text>
        </Box>
      )}
      {confirm && (
        <Box flexDirection="column">
          <Text color={Theme.stateBlocked}>{confirm.message}</Text>
          <Text color={Theme.textChrome}>
            y confirm {confirm.label} · n cancel
          </Text>
        </Box>
      )}
      {commitMode && (
        <Box>
          <Text color={Theme.textChrome}>commit: </Text>
          <Text color={Theme.textPrimary}>{commitMessage}</Text>
          <Text color={Theme.stateActive}>█</Text>
          <Text color={Theme.textChrome}> enter commit · esc cancel</Text>
        </Box>
      )}

      <Text color={Theme.textChrome}>
        {showSidebarLayout && pane === "diff"
          ? "↑↓ scroll · ←/esc files · "
          : "↑↓ file · enter diff · "}
        {scope === "turn"
          ? "t working"
          : `space ${
              files[fileIndex]?.staged && !files[fileIndex]?.unstaged
                ? "unstage"
                : "stage"
            } · x discard · c commit · t turn`}{" "}
        · r refresh
        {scope === "working" && repos.length > 1 ? " · ] repo" : ""} · esc{" "}
        {showSidebarLayout && pane === "diff" ? "files" : "chat"}
      </Text>
    </Box>
  );
};
