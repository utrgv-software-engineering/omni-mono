/** Data for the session's side panels, as the runtime reports it. */

export type EscalationInfo = {
  request_id: string;
  message: string;
  session_id?: string;
  run_id?: string;
};

export type GoalSnapshot = {
  goal: string;
  turn: number;
  max_turns: number;
  tick_interval?: number;
  last_reason: string | null;
  status: 'active' | 'completed' | 'cancelled' | 'paused';
  started_at: number;
  completion_reason: string | null;
  // Auditable artifact attached on terminal states (achieved/blocked).
  evidence?: string | null;
};

export type LoopTaskSnapshot = {
  id: string;
  prompt: string;
  mode: 'dynamic' | 'fixed_interval' | 'fixed_cron';
  cron: string | null;
  interval_seconds: number | null;
  recurring: boolean;
  created_at: number;
  expires_at: number | null;
  next_fire_at: number | null;
  fires: number;
  status: 'active' | 'cancelled' | 'expired' | 'completed';
  last_fire_at: number | null;
  last_reason: string | null;
  jitter_seconds: number;
};

export type NotificationInfo = {
  id: string;
  message: string;
  timestamp: number;
};

export type RecapInfo = {
  text: string;
  timestamp: number;
};

export type WakeupSnapshot = {
  message: string;
  interval_seconds: number;
  recurring: boolean;
  end_at: number | null;
  end_after: number | null;
  fires: number;
  started_at: number;
  status: 'active' | 'cancelled';
};
