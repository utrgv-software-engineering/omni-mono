import type { EscalationInfo } from '@omniagents/client/session/panel-types';
import { Button } from '@omniagents/ui/button';
import { Input } from '@omniagents/ui/input';
import { useState } from 'react';

type Props = {
  escalation: EscalationInfo | null;
  onRespond?: (text: string) => Promise<unknown>;
};

// Banner shown when the agent calls the ``escalate`` builtin (a
// blocking client-function tool). The agent's tool call is paused on
// our pending ``client_response``; the next non-slash message the user
// submits becomes the reply (see App.tsx handleSubmit interceptor).
// Renders nothing when no escalation is pending.
export function EscalationBanner({ escalation, onRespond }: Props) {
  const [answer, setAnswer] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!escalation) {
    return null;
  }

  return (
    <div className="px-3 pt-2">
      <div className="rounded-md border border-warning/60 bg-warning/10 p-2.5">
        <div className="text-xs text-warning font-medium">
          {onRespond ? 'Agent needs your input' : 'Agent escalated — your next message will be sent back as the reply.'}
        </div>
        <div className="mt-1 text-sm text-foreground">{escalation.message}</div>
        {onRespond && (
          <form
            className="mt-2 flex flex-col gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!answer.trim() || sending) return;
              setSending(true);
              setError(null);
              try {
                await onRespond(answer);
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : 'Could not answer the question');
                setSending(false);
              }
            }}
          >
            <Input
              aria-label="Answer the pending question"
              value={answer}
              disabled={sending}
              onChange={(event) => setAnswer(event.target.value)}
            />
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={sending || !answer.trim()}>
              {sending ? 'Sending…' : 'Send answer'}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
