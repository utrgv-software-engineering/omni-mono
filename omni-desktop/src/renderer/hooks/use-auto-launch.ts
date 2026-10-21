import { useStore } from '@nanostores/react';
import { useActorRef, useSelector } from '@xstate/react';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { fromCallback } from 'xstate';

import {
  $omniRuntimeReadiness,
  ensureRuntimeReady,
  type OmniRuntimeReadiness,
  retryRuntimeCheck,
} from '@/renderer/features/Omni/state';
import { $agentStatuses, agentProcessApi, clearStatus } from '@/renderer/services/agent-process';
import { emitter } from '@/renderer/services/ipc';
import { $initialized, persistedStoreApi } from '@/renderer/services/store';
import { type AutoLaunchEvent, autoLaunchMachine, type AutoLaunchPhase } from '@/shared/machines/auto-launch.machine';
import { createMachineLogger } from '@/shared/machines/machine-logger';

export type { AutoLaunchPhase };

type UseAutoLaunchOptions = {
  /** Unique process identifier — "chat" or a CodeTabId. */
  processId: string;
  /** Workspace directory. When null the machine won't launch. */
  workspaceDir: string | null;
  /** Project id, forwarded to ProcessManager to resolve the project's sources. */
  projectId?: string;
  /** Conversation identity; becomes the AgentHost thread id. */
  sessionId?: string;
  /** Logger tag. */
  logLabel?: string;
};

/**
 * Unified auto-launch hook. Drives the lifecycle of any agent process.
 *
 * Side effects are driven by XState invoke actors — no phase-gated useEffects.
 */
export const useAutoLaunch = (opts: UseAutoLaunchOptions) => {
  const { processId, logLabel } = opts;
  const initialized = useStore($initialized);
  const store = useStore(persistedStoreApi.$atom);

  // Refs so actor callbacks always see current values without recreating the actors object
  const processIdRef = useRef(processId);
  processIdRef.current = processId;
  const workspaceDirRef = useRef(opts.workspaceDir);
  workspaceDirRef.current = opts.workspaceDir;
  const previousWorkspaceDirRef = useRef(opts.workspaceDir);
  const projectIdRef = useRef(opts.projectId);
  projectIdRef.current = opts.projectId;
  const sessionIdRef = useRef(opts.sessionId);
  sessionIdRef.current = opts.sessionId;
  const storeRef = useRef(store);
  storeRef.current = store;

  const actors = useMemo(
    () => ({
      checkRuntime: fromCallback<AutoLaunchEvent>(({ sendBack }) => {
        const check = (val: OmniRuntimeReadiness) => {
          if (val.status === 'ready') {
            sendBack({ type: 'RUNTIME_READY' });
            return true;
          }
          if (val.status === 'installing') {
            sendBack({ type: 'RUNTIME_OUTDATED' });
            return true;
          }
          if (val.status === 'error') {
            sendBack({ type: 'RUNTIME_CHECK_FAILED', error: val.error });
            return true;
          }
          return false;
        };

        const current = $omniRuntimeReadiness.get();
        if (current.status === 'idle') {
          ensureRuntimeReady();
        } else if (current.status === 'error') {
          retryRuntimeCheck();
        }

        if (check($omniRuntimeReadiness.get())) {
          return () => {};
        }
        const unsub = $omniRuntimeReadiness.subscribe((val) => check(val));
        return unsub;
      }),

      watchInstallStatus: fromCallback<AutoLaunchEvent>(({ sendBack }) => {
        const unsub = $omniRuntimeReadiness.subscribe((val) => {
          if (val.status === 'ready') {
            sendBack({ type: 'INSTALL_COMPLETED' });
          } else if (val.status === 'error') {
            sendBack({ type: 'INSTALL_FAILED', error: val.error });
          } else if (val.status === 'idle') {
            sendBack({ type: 'INSTALL_CANCELLED' });
          }
        });
        return unsub;
      }),

      checkConfigAndStart: fromCallback<AutoLaunchEvent>(({ sendBack }) => {
        const wd = workspaceDirRef.current;
        if (!wd) {
          sendBack({ type: 'CONFIG_MISSING' });
          return;
        }
        let cancelled = false;
        (async () => {
          try {
            const modelsConfig = await emitter.invoke('settings:get-models-config');
            const hasProviders = Object.keys(modelsConfig.providers).length > 0;
            if (cancelled) {
              return;
            }
            if (!hasProviders) {
              await persistedStoreApi.setKey('onboardingComplete', false);
              sendBack({ type: 'CONFIG_MISSING' });
              return;
            }
          } catch {
            if (cancelled) {
              return;
            }
          }
          if (cancelled) {
            return;
          }

          const existing = $agentStatuses.get()[processIdRef.current];
          if (
            existing &&
            (existing.type === 'running' || existing.type === 'connecting' || existing.type === 'starting')
          ) {
            sendBack({ type: 'CONFIG_OK' });
            return;
          }

          // Drop any stale terminal status from a previous run of this
          // processId (relaunch paths stop first, leaving an `exited` entry).
          // watchProcessStatus subscribes with an immediate replay of the
          // current value, so a leftover `exited` would bounce the machine
          // straight back to idle the moment `starting` is entered.
          clearStatus(processIdRef.current);
          sendBack({ type: 'CONFIG_OK' });
        })();
        return () => {
          cancelled = true;
        };
      }),

      startProcess: fromCallback<AutoLaunchEvent>(({ sendBack }) => {
        const wd = workspaceDirRef.current;
        if (!wd) {
          sendBack({ type: 'AGENT_ERROR', error: 'Workspace is unavailable' });
          return;
        }
        let cancelled = false;
        void agentProcessApi
          .start(processIdRef.current, {
            workspaceDir: wd,
            ...(projectIdRef.current ? { projectId: projectIdRef.current } : {}),
            ...(sessionIdRef.current ? { sessionId: sessionIdRef.current } : {}),
          })
          .catch((error: unknown) => {
            if (!cancelled) {
              sendBack({
                type: 'AGENT_ERROR',
                error: error instanceof Error ? error.message : String(error),
              });
            }
          });
        return () => {
          cancelled = true;
        };
      }),

      watchProcessStatus: fromCallback<AutoLaunchEvent>(({ sendBack }) => {
        let cancelled = false;
        let lastSentType: string | null = null;

        // Seed with current server-side status
        const watchedProcessId = processIdRef.current;
        const seededFrom = $agentStatuses.get()[watchedProcessId];
        emitter
          .invoke('agent-process:get-status', watchedProcessId)
          .then((status) => {
            if (
              cancelled ||
              !status ||
              status.type === 'uninitialized' ||
              $agentStatuses.get()[watchedProcessId] !== seededFrom
            ) {
              return;
            }
            $agentStatuses.setKey(watchedProcessId, status);
          })
          .catch(() => {});

        const unsub = $agentStatuses.subscribe((allStatuses) => {
          const status = allStatuses[watchedProcessId];
          if (!status) {
            return;
          }
          if (status.type === lastSentType) {
            return;
          }
          lastSentType = status.type;
          if (status.type === 'running' || status.type === 'connecting') {
            sendBack({ type: 'AGENT_RUNNING' });
          } else if (status.type === 'error') {
            sendBack({ type: 'AGENT_ERROR', error: status.error.message });
          } else if (status.type === 'exited') {
            sendBack({ type: 'AGENT_EXITED' });
          } else if (status.type === 'uninitialized') {
            // Only authoritative reconnect reconciliation publishes this
            // status for a previously live process. Preserve the user's
            // launch intent, but discard the old runtime identity.
            sendBack({ type: 'AGENT_LOST' });
          }
        });
        return () => {
          cancelled = true;
          unsub();
        };
      }),
    }),
    []
  );

  const inspect = useMemo(
    () =>
      createMachineLogger(logLabel ?? `autoLaunch:${processId}`, {
        tags: { tab: processId },
      }),
    [processId, logLabel]
  );

  const machine = useMemo(() => autoLaunchMachine.provide({ actors }), [actors]);
  const actor = useActorRef(machine, { inspect });
  const phase = useSelector(actor, (snap) => snap.value as AutoLaunchPhase);
  const error = useSelector(actor, (snap) => snap.context.error);

  // Trigger: send LAUNCH when initialized + workspace available
  useEffect(() => {
    if (!initialized || !opts.workspaceDir) {
      return;
    }
    const snap = actor.getSnapshot();
    if (snap.value === 'idle' && !snap.context.hasLaunched) {
      actor.send({ type: 'LAUNCH' });
    }
  }, [initialized, opts.workspaceDir, actor]);

  // Reset when the registered Workspace definition changes.
  useEffect(() => {
    const previousWorkspaceDir = previousWorkspaceDirRef.current;
    previousWorkspaceDirRef.current = opts.workspaceDir;

    const workspaceChanged = Boolean(previousWorkspaceDir && previousWorkspaceDir !== opts.workspaceDir);
    if (!initialized || !opts.workspaceDir || !workspaceChanged) {
      return;
    }

    let cancelled = false;
    void (async () => {
      if (cancelled) {
        return;
      }
      await agentProcessApi.stop(processIdRef.current);
      if (cancelled) {
        return;
      }
      actor.send({ type: 'RESET' });
      // Drive the relaunch explicitly: the auto-launch effect's deps don't
      // change on RESET, so without this a workspace switch (new chat
      // conversation, session switch) can strand the machine in idle.
      actor.send({ type: 'LAUNCH' });
    })();

    return () => {
      cancelled = true;
    };
  }, [initialized, opts.workspaceDir, actor]);

  const retry = useCallback(() => {
    actor.send({ type: 'RETRY' });
  }, [actor]);

  const launch = useCallback(() => {
    if (!opts.workspaceDir) {
      return;
    }
    actor.send({ type: 'RELAUNCH' });
  }, [opts.workspaceDir, actor]);

  return { phase, error, retry, launch, actor };
};
