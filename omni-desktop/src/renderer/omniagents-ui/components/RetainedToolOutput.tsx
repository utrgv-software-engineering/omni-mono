import { useState } from 'react';

import { Button } from '@/renderer/ds/ui/button';
import { Field, FieldLabel } from '@/renderer/ds/ui/field';
import { Input } from '@/renderer/ds/ui/input';
import { useRPCClient } from '@/renderer/omniagents-ui/rpc-context';

export type Retention = {
  status: 'saved' | 'unavailable';
  reference?: string;
  session_id?: string;
  original_bytes?: number;
};

type OutputPage = {
  text: string;
  offset: number;
  next_offset: number | null;
  match_offset?: number | null;
  next_search_offset?: number;
};

export function RetainedToolOutput({ retention }: { retention: Retention }) {
  if (retention.status !== 'saved' || !retention.reference || !retention.session_id) {
    return <p role="status">Output shortened. The full result could not be saved.</p>;
  }
  return <SavedOutput retention={retention} />;
}

function SavedOutput({ retention }: { retention: Retention }) {
  const rpc = useRPCClient();
  const [page, setPage] = useState<OutputPage | null>(null);
  const [query, setQuery] = useState('');
  const [activeQuery, setActiveQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const read = async (offset: number, search = '') => {
    setBusy(true);
    setError(null);
    try {
      const result = await rpc.serverCall(
        'tool_outputs.read',
        {
          reference: retention.reference,
          offset,
          limit: 8000,
          ...(search ? { query: search } : {}),
        },
        retention.session_id
      );
      if (typeof result.text !== 'string' || typeof result.offset !== 'number') {
        throw new Error(typeof result.error === 'string' ? result.error : 'Saved output could not be read.');
      }
      setPage(result as OutputPage);
      setActiveQuery(search);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Saved output could not be read.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label="Saved tool output" className="flex min-w-0 flex-col gap-2">
      <p>Output shortened for the agent. The full text is saved.</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void read(0)}>
          Read saved output
        </Button>
        {page?.next_offset != null && (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => void read(page.next_offset!)}>
            Next page
          </Button>
        )}
      </div>
      {page && (
        <>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void read(0, query);
            }}
          >
            <Field>
              <FieldLabel htmlFor={`output-search-${retention.reference}`}>Find in saved output</FieldLabel>
              <Input
                id={`output-search-${retention.reference}`}
                value={query}
                maxLength={500}
                onChange={(event) => setQuery(event.target.value)}
              />
            </Field>
            <Button type="submit" variant="outline" size="sm" disabled={busy || !query}>
              Find
            </Button>
            {activeQuery && page.next_search_offset != null && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void read(page.next_search_offset!, activeQuery)}
              >
                Next match
              </Button>
            )}
          </form>
          <p>{page.match_offset === null ? 'No match found.' : `Showing text from character ${page.offset}.`}</p>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all">{page.text}</pre>
        </>
      )}
      {busy && <p role="status">Loading saved output…</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
