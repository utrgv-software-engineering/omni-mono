import React, { useCallback, useEffect, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type {
  GitBranch,
  GitCommitSummary,
  GitConflict,
  GitDiffFile,
  GitRepository,
  GitStatusResult,
  GitWorktree,
  RPCClient,
} from "../rpc/client.js";
import {
  buildFileRows,
  clampScroll,
  confirmationOf,
  fileCounts,
  flattenConflict,
  flattenDiff,
  headline,
  orderBranches,
  rowColor,
  type GitFileRow,
} from "./git-model.js";
import { SelectableRow } from "./shared/SelectableRow.js";
import { Theme } from "../theme.js";

const MAX_VISIBLE_FILES = 10;
const DIFF_WINDOW_LINES = 18;
const DIFF_SCROLL_PAGE = 12;

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  client: RPCClient | null;
};

/** A pending -32093 confirmation: re-run the operation echoing the token. */
type PendingConfirmation = {
  label: string;
  message: string;
  token: string;
  /** Impact paths from the server — what dies if the user presses y. */
  paths: string[];
  run: (token: string) => Promise<unknown>;
};

const MAX_IMPACT_PATHS = 8;

// Docked git panel (Ctrl+G) — repository status, a per-file diff viewer,
// staging/commit, and remote sync over the typed git_* RPCs, routed
// through the AgentHost's default environment. Renders nothing while
// unfocused: unlike bash jobs there is no ambient signal worth a collapsed
// row; the chord is the entry point. Guarded operations answer `-32093
// git_confirmation_required` with a single-use token; the y/n prompt here
// echoes it back. Discard/reset stay agent-side for now — the panel only
// exposes non-destructive writes.
export const GitPanel = React.memo(({ focused, onUnfocus, client }: Props) => {
  const [repos, setRepos] = useState<GitRepository[]>([]);
  const [repoIndex, setRepoIndex] = useState(0);
  const [status, setStatus] = useState<GitStatusResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  // Diff view state — when diffFile is set the panel shows a windowed,
  // scrollable diff for one file instead of the row list.
  const [diffFile, setDiffFile] = useState<GitDiffFile | null>(null);
  const [diffForPath, setDiffForPath] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffScroll, setDiffScroll] = useState(0);
  // Write/network op state.
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [commitMode, setCommitMode] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [confirm, setConfirm] = useState<PendingConfirmation | null>(null);
  // Branch switcher state.
  const [branchMode, setBranchMode] = useState(false);
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [branchIndex, setBranchIndex] = useState(0);
  const [branchError, setBranchError] = useState<string | null>(null);
  const [branchLoading, setBranchLoading] = useState(false);
  const [newBranchMode, setNewBranchMode] = useState(false);
  const [newBranchName, setNewBranchName] = useState("");
  // Conflict detail for the open path (rendered in the diff window).
  const [conflict, setConflict] = useState<GitConflict | null>(null);
  // Commit log view (also the rev picker for reset).
  const [logMode, setLogMode] = useState(false);
  const [commits, setCommits] = useState<GitCommitSummary[]>([]);
  const [logIndex, setLogIndex] = useState(0);
  const [logLoading, setLogLoading] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);
  const [logDetail, setLogDetail] = useState(false);
  const [resetPrompt, setResetPrompt] = useState(false);
  // Worktrees view.
  const [worktreesMode, setWorktreesMode] = useState(false);
  const [worktrees, setWorktrees] = useState<GitWorktree[]>([]);
  const [worktreesLoading, setWorktreesLoading] = useState(false);
  const [worktreesError, setWorktreesError] = useState<string | null>(null);

  const granted = !!client?.isGranted("git_status");
  const environmentId = client?.defaultEnvironmentId() ?? null;
  const repo = repos[repoIndex]?.repo ?? null;

  const loadStatus = useCallback(
    async (repoOverride?: string) => {
      if (!client || !environmentId) return;
      const target = repoOverride ?? repo;
      setLoading(true);
      setError(null);
      try {
        let repoList = repos;
        if (repoList.length === 0 || repoOverride === undefined) {
          const listed = await client.gitListRepositories(environmentId);
          repoList = listed.repositories;
          setRepos(repoList);
        }
        const statusRepo = target ?? repoList[0]?.repo;
        if (statusRepo === undefined) {
          setStatus(null);
          setError("no git repositories in this environment");
          return;
        }
        const result = await client.gitStatus(environmentId, statusRepo, {
          includeUntracked: true,
        });
        setStatus(result);
      } catch (e) {
        setError((e as Error).message ?? String(e));
      } finally {
        setLoading(false);
      }
    },
    [client, environmentId, repo, repos],
  );

  // Load on focus; drop the stale view on unfocus so reopening refetches.
  useEffect(() => {
    if (!focused) {
      setDiffFile(null);
      setDiffForPath(null);
      setDiffError(null);
      setConflict(null);
      setBranchMode(false);
      setNewBranchMode(false);
      setNewBranchName("");
      setConfirm(null);
      setCommitMode(false);
      setCommitMessage("");
      setLogMode(false);
      setLogDetail(false);
      setResetPrompt(false);
      setWorktreesMode(false);
      return;
    }
    if (!granted || !environmentId) return;
    void loadStatus();
    // Deps intentionally omit loadStatus: refetch on focus only — an
    // explicit 'r' refreshes after that.
  }, [focused, granted, environmentId]);

  const rows: GitFileRow[] = status ? buildFileRows(status) : [];
  useEffect(() => {
    setSelectedIndex((prev) =>
      rows.length === 0 ? 0 : Math.min(prev, rows.length - 1),
    );
  }, [rows.length]);

  const loadDiff = useCallback(
    async (row: GitFileRow) => {
      if (!client || !environmentId || !repo) return;
      setDiffForPath(row.path);
      setDiffFile(null);
      setDiffError(null);
      setConflict(null);
      setDiffScroll(0);
      if (row.untracked) {
        setDiffError("untracked file — no diff until it is staged");
        return;
      }
      // Conflicted rows show the merge conflict regions, not a diff — the
      // three-way content is what the user needs to resolve.
      if (row.conflicted) {
        setDiffLoading(true);
        try {
          const result = await client.gitConflicts(environmentId, repo, [
            row.path,
          ]);
          const found =
            result.conflicts.find((c) => c.path === row.path) ??
            result.conflicts[0] ??
            null;
          if (!found) {
            setDiffError("no conflict details for this path");
          } else {
            setConflict(found);
          }
        } catch (e) {
          setDiffError((e as Error).message ?? String(e));
        } finally {
          setDiffLoading(false);
        }
        return;
      }
      setDiffLoading(true);
      try {
        // Staged-only changes live in the index; everything else diffs
        // the worktree. (A file with both kinds of change shows its
        // worktree side — the view of what is not yet staged.)
        const mode = row.staged && !row.unstaged ? "staged" : "worktree";
        const result = await client.gitDiff(environmentId, repo, {
          mode,
          paths: [row.renamedFrom ?? row.path, row.path],
        });
        const file =
          result.files.find((f) => f.path === row.path) ??
          result.files[0] ??
          null;
        if (!file) {
          setDiffError("no diff for this path");
        } else {
          setDiffFile(file);
        }
      } catch (e) {
        setDiffError((e as Error).message ?? String(e));
      } finally {
        setDiffLoading(false);
      }
    },
    [client, environmentId, repo],
  );

  const diffLines = conflict
    ? flattenConflict(conflict)
    : diffFile
      ? flattenDiff(diffFile)
      : [];

  // Transient op feedback auto-clears; errors stick a little longer than
  // successes would need, but a fixed window keeps this dumb and safe.
  useEffect(() => {
    if (!statusLine || busy) return;
    const id = setTimeout(() => setStatusLine(null), 4000);
    return () => clearTimeout(id);
  }, [statusLine, busy]);

  // Long-running remote operations stream progress; surface the latest
  // phase in the status row while the panel is open.
  useEffect(() => {
    if (!focused || !client) return;
    const onProgress = (params: { operation: string; phase: string }) => {
      setStatusLine(`${params.operation}: ${params.phase}`);
    };
    client.on("git_operation_progress", onProgress);
    return () => {
      client.off("git_operation_progress", onProgress);
    };
  }, [focused, client]);

  /**
   * Run a write/network operation: report progress, refresh status on
   * success, and turn a `-32093 git_confirmation_required` rejection into
   * a y/n prompt that re-runs the operation echoing the single-use token.
   */
  const runOp = useCallback(
    async (label: string, op: (token?: string) => Promise<unknown>) => {
      setBusy(true);
      setStatusLine(`${label}…`);
      try {
        await op();
        setStatusLine(`${label}: done`);
        await loadStatus();
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
    [loadStatus],
  );

  const openBranches = useCallback(async () => {
    if (!client || !environmentId || !repo) return;
    setBranchMode(true);
    setBranchIndex(0);
    setBranchError(null);
    setBranchLoading(true);
    try {
      const result = await client.gitListBranches(environmentId, repo);
      setBranches(orderBranches(result.branches));
    } catch (e) {
      setBranchError((e as Error).message ?? String(e));
    } finally {
      setBranchLoading(false);
    }
  }, [client, environmentId, repo]);

  const checkoutBranch = useCallback(
    (name: string, create: boolean) => {
      if (!client || !environmentId || !repo) return;
      setBranchMode(false);
      setNewBranchMode(false);
      setNewBranchName("");
      void runOp(`checkout ${name}`, (token) =>
        client.gitCheckout(environmentId, repo, name, {
          create: create || undefined,
          confirmationToken: token,
        }),
      );
    },
    [client, environmentId, repo, runOp],
  );

  // Branch switcher layer.
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "q") {
        setBranchMode(false);
        setBranchError(null);
        return true;
      }
      if (input === "r") {
        void openBranches();
        return true;
      }
      if (input === "n") {
        setNewBranchMode(true);
        return true;
      }
      if (branches.length === 0) return true;
      if (key.upArrow || input === "k") {
        setBranchIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return true;
      }
      if (key.downArrow || input === "j") {
        setBranchIndex((prev) =>
          prev < branches.length - 1 ? prev + 1 : prev,
        );
        return true;
      }
      if (key.return) {
        const branch = branches[branchIndex];
        if (!branch) return true;
        if (branch.current) {
          setStatusLine(`already on ${branch.name}`);
          setBranchMode(false);
          return true;
        }
        checkoutBranch(branch.name, false);
        return true;
      }
      return true; // modal view — swallow the rest
    },
    {
      isActive: focused && branchMode && !newBranchMode && !confirm,
      priority: KeypressPriority.High,
    },
  );

  // New-branch name input layer (same shape as the commit input).
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape) {
        setNewBranchMode(false);
        setNewBranchName("");
        return true;
      }
      if (key.return) {
        const name = newBranchName.trim();
        if (!name) {
          setStatusLine("branch name is empty");
          return true;
        }
        checkoutBranch(name, true);
        return true;
      }
      if (key.backspace || key.delete) {
        setNewBranchName((prev) => prev.slice(0, -1));
        return true;
      }
      if (input && !key.ctrl && !key.meta && input !== " ") {
        setNewBranchName((prev) => prev + input);
        return true;
      }
      return true;
    },
    {
      isActive: focused && newBranchMode,
      priority: KeypressPriority.Critical,
    },
  );

  const openLog = useCallback(async () => {
    if (!client || !environmentId || !repo) return;
    setLogMode(true);
    setLogIndex(0);
    setLogDetail(false);
    setLogError(null);
    setLogLoading(true);
    try {
      const result = await client.gitLog(environmentId, repo, {
        maxCount: 30,
      });
      setCommits(result.commits);
    } catch (e) {
      setLogError((e as Error).message ?? String(e));
    } finally {
      setLogLoading(false);
    }
  }, [client, environmentId, repo]);

  const resetTo = useCallback(
    (commit: GitCommitSummary, mode: "soft" | "mixed" | "hard") => {
      if (!client || !environmentId || !repo) return;
      setLogMode(false);
      setLogDetail(false);
      void runOp(`reset --${mode} ${commit.short_oid}`, (token) =>
        client.gitReset(environmentId, repo, {
          mode,
          rev: commit.oid,
          confirmationToken: token,
        }),
      );
    },
    [client, environmentId, repo, runOp],
  );

  // Log view layer — also the rev picker for reset.
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "q") {
        if (logDetail) {
          setLogDetail(false);
        } else {
          setLogMode(false);
          setLogError(null);
        }
        return true;
      }
      if (input === "r") {
        void openLog();
        return true;
      }
      if (commits.length === 0) return true;
      if (key.upArrow || input === "k") {
        setLogIndex((prev) => (prev > 0 ? prev - 1 : prev));
        setLogDetail(false);
        return true;
      }
      if (key.downArrow || input === "j") {
        setLogIndex((prev) => (prev < commits.length - 1 ? prev + 1 : prev));
        setLogDetail(false);
        return true;
      }
      if (key.return) {
        setLogDetail((prev) => !prev);
        return true;
      }
      // Reset lives behind a deliberate shifted chord + sub-prompt.
      // Bare s/m/h once sat next to the j/k motion keys — one vim
      // reflex away from `reset --hard`.
      if (input === "R" && commits[logIndex]) {
        setResetPrompt(true);
        return true;
      }
      return true; // modal view — swallow the rest
    },
    {
      isActive: focused && logMode && !confirm && !resetPrompt,
      priority: KeypressPriority.High,
    },
  );

  // Reset-mode sub-prompt: s/m/h choose the mode, esc/n backs out.
  useKeypress(
    (input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "n" || input === "q") {
        setResetPrompt(false);
        return true;
      }
      const commit = commits[logIndex];
      if (commit && (input === "s" || input === "m" || input === "h")) {
        setResetPrompt(false);
        resetTo(
          commit,
          input === "s" ? "soft" : input === "m" ? "mixed" : "hard",
        );
        return true;
      }
      return true; // modal prompt — swallow the rest
    },
    {
      isActive: focused && logMode && resetPrompt,
      priority: KeypressPriority.Critical,
    },
  );

  const openWorktrees = useCallback(async () => {
    if (!client || !environmentId || !repo) return;
    setWorktreesMode(true);
    setWorktreesError(null);
    setWorktreesLoading(true);
    try {
      const result = await client.gitListWorktrees(environmentId, repo);
      setWorktrees(result.worktrees);
    } catch (e) {
      setWorktreesError((e as Error).message ?? String(e));
    } finally {
      setWorktreesLoading(false);
    }
  }, [client, environmentId, repo]);

  // Worktrees view layer — informational, no row actions.
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "q") {
        setWorktreesMode(false);
        setWorktreesError(null);
        return true;
      }
      if (input === "r") {
        void openWorktrees();
        return true;
      }
      return true; // modal view — swallow the rest
    },
    {
      isActive: focused && worktreesMode,
      priority: KeypressPriority.High,
    },
  );

  // Confirmation layer: y re-runs with the token, n / esc cancels.
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
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
      return true; // modal — swallow everything else
    },
    {
      isActive: focused && confirm !== null,
      priority: KeypressPriority.Critical,
    },
  );

  // Commit-message input layer. Deliberately built on the panel's own
  // keypress system (not ink-text-input) so it participates in the same
  // priority routing as every other layer and nothing double-handles.
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
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
      if (key.backspace || key.delete) {
        setCommitMessage((prev) => prev.slice(0, -1));
        return true;
      }
      if (input && !key.ctrl && !key.meta) {
        setCommitMessage((prev) => prev + input);
        return true;
      }
      return true;
    },
    {
      isActive: focused && commitMode,
      priority: KeypressPriority.Critical,
    },
  );

  // Diff-view keyboard layer: scroll with ↑↓/PgUp/PgDn, esc back to rows.
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "q") {
        setDiffFile(null);
        setDiffForPath(null);
        setDiffError(null);
        setConflict(null);
        return true;
      }
      const step = key.pageDown || key.pageUp ? DIFF_SCROLL_PAGE : 1;
      if (key.downArrow || key.pageDown || input === "j") {
        setDiffScroll((prev) =>
          clampScroll(prev + step, diffLines.length, DIFF_WINDOW_LINES),
        );
        return true;
      }
      if (key.upArrow || key.pageUp || input === "k") {
        setDiffScroll((prev) =>
          clampScroll(prev - step, diffLines.length, DIFF_WINDOW_LINES),
        );
        return true;
      }
      return true; // swallow everything else while the diff is open
    },
    {
      isActive: focused && diffForPath !== null,
      priority: KeypressPriority.Critical,
    },
  );

  // Row-list keyboard layer.
  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "q") {
        onUnfocus();
        return true;
      }
      if (input === "r") {
        void loadStatus();
        return true;
      }
      if (input === "]" && repos.length > 1) {
        const next = (repoIndex + 1) % repos.length;
        setRepoIndex(next);
        setSelectedIndex(0);
        void loadStatus(repos[next]!.repo);
        return true;
      }
      // Remote sync works on a clean tree too, so these come before the
      // row guard. Uppercase P is push — matching the drift arrows: pull
      // is the frequent/safe one, push gets the deliberate shift chord.
      if (client && environmentId && repo && !busy) {
        if (input === "f") {
          void runOp("fetch", () => client.gitFetch(environmentId, repo));
          return true;
        }
        if (input === "p") {
          void runOp("pull", () => client.gitPull(environmentId, repo));
          return true;
        }
        if (input === "P") {
          void runOp("push", (token) =>
            client.gitPush(environmentId, repo, {
              confirmationToken: token,
            }),
          );
          return true;
        }
        if (input === "c") {
          if (fileCounts(rows).staged === 0) {
            setStatusLine("nothing staged to commit");
          } else {
            setCommitMode(true);
          }
          return true;
        }
        if (input === "b") {
          void openBranches();
          return true;
        }
        if (input === "l") {
          void openLog();
          return true;
        }
        if (input === "w") {
          void openWorktrees();
          return true;
        }
      }
      if (rows.length === 0) return false;
      if (key.upArrow || input === "k") {
        setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return true;
      }
      if (key.downArrow || input === "j") {
        setSelectedIndex((prev) => (prev < rows.length - 1 ? prev + 1 : prev));
        return true;
      }
      if (key.return || input === "d") {
        const row = rows[selectedIndex];
        if (row) void loadDiff(row);
        return true;
      }
      if (client && environmentId && repo && !busy) {
        const row = rows[selectedIndex];
        if (row && input === "s") {
          void runOp(`stage ${row.path}`, () =>
            client.gitStage(environmentId, repo, { paths: [row.path] }),
          );
          return true;
        }
        if (row && input === "u") {
          if (row.untracked) {
            setStatusLine("untracked file — nothing staged to unstage");
            return true;
          }
          void runOp(`unstage ${row.path}`, () =>
            client.gitUnstage(environmentId, repo, { paths: [row.path] }),
          );
          return true;
        }
        if (row && input === "x") {
          if (row.untracked) {
            // git_discard restores tracked content; it does not delete
            // files git never knew about.
            setStatusLine("untracked file — delete it in the shell");
            return true;
          }
          // Always answers -32093 first: the y/n prompt with the
          // server's impact paths is the real gate.
          void runOp(`discard ${row.path}`, (token) =>
            client.gitDiscard(environmentId, repo, {
              paths: [row.path],
              confirmationToken: token,
            }),
          );
          return true;
        }
      }
      return false;
    },
    {
      isActive:
        focused &&
        diffForPath === null &&
        !commitMode &&
        !confirm &&
        !branchMode &&
        !logMode &&
        !worktreesMode,
      priority: KeypressPriority.High,
    },
  );

  if (!focused) return null;

  if (!granted) {
    return (
      <Panel borderColor={Theme.stateBlocked}>
        <Text color={Theme.stateBlocked}>
          git operations are not supported by this server
        </Text>
        <Text color={Theme.textChrome}>esc to close</Text>
      </Panel>
    );
  }
  if (!environmentId) {
    return (
      <Panel borderColor={Theme.stateBlocked}>
        <Text color={Theme.stateBlocked}>
          no execution environment routed for this session
        </Text>
        <Text color={Theme.textChrome}>esc to close</Text>
      </Panel>
    );
  }

  const counts = status ? fileCounts(rows) : null;

  return (
    <Panel borderColor={Theme.stateActive}>
      <Box>
        <Text color={Theme.textChrome}>Git </Text>
        {repo !== null && (
          <Text color={Theme.textPrimary} bold>
            {repo === "." ? (repos[repoIndex]?.root ?? ".") : repo}
          </Text>
        )}
        {status && (
          <Text color={Theme.textChrome}> {headline(status)}</Text>
        )}
        {loading && (
          <Text color={Theme.stateActive}>
            {" "}
            <Spinner type="dots" />
          </Text>
        )}
      </Box>

      {error && <Text color={Theme.stateFailed}>{error}</Text>}

      {status && counts && (
        <Box>
          <Text color={Theme.stateDone}>{counts.staged}</Text>
          <Text color={Theme.textChrome}> staged · </Text>
          <Text color={Theme.stateActive}>{counts.unstaged}</Text>
          <Text color={Theme.textChrome}> unstaged · </Text>
          <Text color={Theme.textChrome}>
            {counts.untracked} untracked
          </Text>
          {counts.conflicted > 0 && (
            <>
              <Text color={Theme.textChrome}> · </Text>
              <Text color={Theme.stateFailed}>
                {counts.conflicted} conflicted
              </Text>
            </>
          )}
        </Box>
      )}

      {status && rows.length === 0 && !loading && (
        <Text color={Theme.stateDone}>working tree clean</Text>
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
          {confirm.paths.slice(0, MAX_IMPACT_PATHS).map((path) => (
            <Text key={path} color={Theme.stateFailed}>
              {"  "}✗ {path}
            </Text>
          ))}
          {confirm.paths.length > MAX_IMPACT_PATHS && (
            <Text color={Theme.stateFailed}>
              {"  "}… +{confirm.paths.length - MAX_IMPACT_PATHS} more
            </Text>
          )}
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
        </Box>
      )}

      {branchMode ? (
        <Box flexDirection="column">
          <Box>
            <Text color={Theme.textChrome}>Branches</Text>
            {branchLoading && (
              <Text color={Theme.stateActive}>
                {" "}
                <Spinner type="dots" />
              </Text>
            )}
          </Box>
          {branchError && (
            <Text color={Theme.stateFailed}>{branchError}</Text>
          )}
          {branches.slice(0, MAX_VISIBLE_FILES).map((branch, idx) => (
            <SelectableRow key={branch.ref} selected={idx === branchIndex && !newBranchMode}>
              <Text
                color={
                  branch.current
                    ? Theme.stateDone
                    : branch.remote
                      ? Theme.textChrome
                      : Theme.textPrimary
                }
                bold={branch.current}
              >
                {branch.current ? "* " : "  "}
                {branch.name}
              </Text>
              {branch.upstream && !branch.remote && (
                <Text color={Theme.textChrome}>
                  {" "}
                  → {branch.upstream}
                </Text>
              )}
              {branch.category === "worker" && (
                <Text color={Theme.stateBlocked}> (worker)</Text>
              )}
            </SelectableRow>
          ))}
          {branches.length > MAX_VISIBLE_FILES && (
            <Text color={Theme.textChrome}>
              … +{branches.length - MAX_VISIBLE_FILES} more
            </Text>
          )}
          {newBranchMode ? (
            <Box>
              <Text color={Theme.textChrome}>new branch: </Text>
              <Text color={Theme.textPrimary}>{newBranchName}</Text>
              <Text color={Theme.stateActive}>█</Text>
            </Box>
          ) : (
            <Text color={Theme.textChrome}>
              ↑↓ select · enter checkout · n new branch · r refresh · esc back
            </Text>
          )}
        </Box>
      ) : logMode ? (
        <Box flexDirection="column">
          <Box>
            <Text color={Theme.textChrome}>Log</Text>
            {logLoading && (
              <Text color={Theme.stateActive}>
                {" "}
                <Spinner type="dots" />
              </Text>
            )}
          </Box>
          {logError && <Text color={Theme.stateFailed}>{logError}</Text>}
          {commits.slice(0, MAX_VISIBLE_FILES).map((commit, idx) => (
            <SelectableRow key={commit.oid} selected={idx === logIndex}>
              <Text color={Theme.stateBlocked}>{commit.short_oid} </Text>
              <Text color={Theme.textPrimary} wrap="truncate-end">
                {commit.subject}
              </Text>
              {commit.refs.length > 0 && (
                <Text color={Theme.stateDone}>
                  {" "}
                  ({commit.refs.join(", ")})
                </Text>
              )}
            </SelectableRow>
          ))}
          {commits.length > MAX_VISIBLE_FILES && (
            <Text color={Theme.textChrome}>
              … +{commits.length - MAX_VISIBLE_FILES} more
            </Text>
          )}
          {logDetail && commits[logIndex] && (
            <Box flexDirection="column" marginTop={1}>
              <Text color={Theme.textChrome}>
                {commits[logIndex]!.author_name} ·{" "}
                {commits[logIndex]!.committed_at}
              </Text>
              {commits[logIndex]!.body && (
                <Text color={Theme.textSecondary}>
                  {commits[logIndex]!.body.trim()}
                </Text>
              )}
            </Box>
          )}
          {resetPrompt && commits[logIndex] ? (
            <Text color={Theme.stateBlocked}>
              reset to {commits[logIndex]!.short_oid}: s soft · m mixed · h
              hard · esc cancel
            </Text>
          ) : (
            <Text color={Theme.textChrome}>
              ↑↓ select · enter details · R reset · r refresh · esc back
            </Text>
          )}
        </Box>
      ) : worktreesMode ? (
        <Box flexDirection="column">
          <Box>
            <Text color={Theme.textChrome}>Worktrees</Text>
            {worktreesLoading && (
              <Text color={Theme.stateActive}>
                {" "}
                <Spinner type="dots" />
              </Text>
            )}
          </Box>
          {worktreesError && (
            <Text color={Theme.stateFailed}>{worktreesError}</Text>
          )}
          {worktrees.slice(0, MAX_VISIBLE_FILES).map((wt) => (
            <Box key={wt.path}>
              <Text color={Theme.textPrimary}>{wt.path}</Text>
              <Text color={Theme.textChrome}>
                {" "}
                {wt.bare
                  ? "(bare)"
                  : wt.detached
                    ? `@ ${wt.head?.slice(0, 8) ?? "?"}`
                    : (wt.branch ?? "?")}
              </Text>
              {wt.locked && (
                <Text color={Theme.stateBlocked}>
                  {" "}
                  locked{wt.lock_reason ? `: ${wt.lock_reason}` : ""}
                </Text>
              )}
              {wt.prunable && (
                <Text color={Theme.stateBlocked}> prunable</Text>
              )}
              {wt.category === "worker" && (
                <Text color={Theme.stateBlocked}> (worker)</Text>
              )}
              {!wt.accessible && (
                <Text color={Theme.textChrome}> read-only</Text>
              )}
            </Box>
          ))}
          {worktrees.length > MAX_VISIBLE_FILES && (
            <Text color={Theme.textChrome}>
              … +{worktrees.length - MAX_VISIBLE_FILES} more
            </Text>
          )}
          <Text color={Theme.textChrome}>r refresh · esc back</Text>
        </Box>
      ) : diffForPath === null ? (
        <>
          {rows.slice(0, MAX_VISIBLE_FILES).map((row, idx) => (
            <SelectableRow key={row.path} selected={idx === selectedIndex}>
              <Text color={rowColor(row)}>{row.xy} </Text>
              <Text color={Theme.textPrimary}>
                {row.renamedFrom ? `${row.renamedFrom} → ${row.path}` : row.path}
              </Text>
            </SelectableRow>
          ))}
          {rows.length > MAX_VISIBLE_FILES && (
            <Text color={Theme.textChrome}>
              … +{rows.length - MAX_VISIBLE_FILES} more
            </Text>
          )}
          {!commitMode && !confirm && (
            <Text color={Theme.textChrome}>
              ↑↓ select · enter diff · s stage · u unstage · x discard · c
              commit · b branches · l log · w worktrees · f fetch · p pull ·
              P push · r refresh
              {repos.length > 1 ? " · ] repo" : ""} · esc exit
            </Text>
          )}
        </>
      ) : (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color={Theme.stateActive} bold>
              {diffForPath}
            </Text>
            {conflict && (
              <Text color={Theme.stateFailed}> conflict</Text>
            )}
            {diffFile && (
              <Text color={Theme.textChrome}>
                {"  "}
                {diffFile.added_lines != null && (
                  <Text color={Theme.stateDone}>
                    +{diffFile.added_lines}
                  </Text>
                )}{" "}
                {diffFile.deleted_lines != null && (
                  <Text color={Theme.stateFailed}>
                    -{diffFile.deleted_lines}
                  </Text>
                )}
              </Text>
            )}
            {diffLines.length > DIFF_WINDOW_LINES && (
              <Text color={Theme.textChrome}>
                {"  "}
                {diffScroll + 1}–
                {Math.min(diffScroll + DIFF_WINDOW_LINES, diffLines.length)} of{" "}
                {diffLines.length}
              </Text>
            )}
          </Box>
          {diffLoading ? (
            <Text color={Theme.textChrome} italic>
              fetching diff…
            </Text>
          ) : diffError ? (
            <Text color={Theme.stateBlocked}>{diffError}</Text>
          ) : (
            diffLines
              .slice(diffScroll, diffScroll + DIFF_WINDOW_LINES)
              .map((line, i) => (
                <Text
                  key={diffScroll + i}
                  color={line.color}
                  bold={line.bold}
                  wrap="truncate-end"
                >
                  {line.text}
                </Text>
              ))
          )}
          <Text color={Theme.textChrome}>
            ↑↓ scroll · pgup/pgdn page · esc back
          </Text>
        </Box>
      )}
    </Panel>
  );
});

const Panel = ({
  borderColor,
  children,
}: {
  borderColor: string;
  children: React.ReactNode;
}) => (
  <Box flexDirection="column" paddingX={1} borderStyle="round" borderColor={borderColor}>
    {children}
  </Box>
);
