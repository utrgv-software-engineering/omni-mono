import { atom } from 'nanostores';

import type { QueuedMessage } from '../rpc/client';
import type { ModelDescriptor } from '../rpc/model-catalog';
import type { BashJobSummary } from '../transcript/activity-store';
import type {
  EscalationInfo,
  GoalSnapshot,
  LoopTaskSnapshot,
  NotificationInfo,
  RecapInfo,
  WakeupSnapshot,
} from './panel-types';

export type SessionPanels = {
  models: ModelDescriptor[];
  activeModel: string | null;
  reasoningEffort: string | null;
  approvalsReviewer: 'user' | 'auto';
  workflowReviewer: 'off' | 'guardian';
  modelLoading: boolean;
  modelMutating: boolean;
  modelError: string | null;
  workspacePath: string | null;
  liveBashJobs: BashJobSummary[] | null;
  goalSnapshot: GoalSnapshot | null;
  wakeupSnapshot: WakeupSnapshot | null;
  loopTasks: LoopTaskSnapshot[];
  notifications: NotificationInfo[];
  recap: RecapInfo | null;
  escalation: EscalationInfo | null;
  queuedMessages: QueuedMessage[];
  dismissedWorkerIds: Set<string>;
  dismissedJobIds: Set<string>;
  dismissedTaskIds: Set<string>;
};

/** Session-owned panel state. Every write is a plain set; a read applies
 * whatever the server answered last. */
export class SessionPanelStore {
  private disposed = false;
  private readonly revisions = new Map<keyof SessionPanels, number>();
  readonly state = atom<SessionPanels>({
    models: [],
    activeModel: null,
    reasoningEffort: null,
    approvalsReviewer: 'user',
    workflowReviewer: 'guardian',
    modelLoading: true,
    modelMutating: false,
    modelError: null,
    workspacePath: null,
    liveBashJobs: null,
    goalSnapshot: null,
    wakeupSnapshot: null,
    loopTasks: [],
    notifications: [],
    recap: null,
    escalation: null,
    queuedMessages: [],
    dismissedWorkerIds: new Set(),
    dismissedJobIds: new Set(),
    dismissedTaskIds: new Set(),
  });
  set = <K extends keyof SessionPanels>(
    key: K,
    update: SessionPanels[K] | ((previous: SessionPanels[K]) => SessionPanels[K])
  ) => {
    if (this.disposed) {
      return;
    }
    const previous = this.state.get();
    const value = typeof update === 'function' ? update(previous[key]) : update;
    this.revisions.set(key, this.revisionOf(key) + 1);
    this.state.set({ ...previous, [key]: value });
  };
  /** Count of writes to *key*; lets a late reply detect a newer write. */
  revisionOf(key: keyof SessionPanels): number {
    return this.revisions.get(key) ?? 0;
  }
  async refresh<K extends keyof SessionPanels>(key: K, read: () => Promise<SessionPanels[K]>) {
    this.set(key, await read());
  }
  dispose() {
    this.disposed = true;
  }
}
