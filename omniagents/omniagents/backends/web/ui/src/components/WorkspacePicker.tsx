import type { RPCClient } from '@omniagents/client/rpc/client';
import { Button } from '@omniagents/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@omniagents/ui/dialog';
import { Input } from '@omniagents/ui/input';
import { Spinner } from '@omniagents/ui/spinner';
import { ArrowUpIcon, FolderIcon, HomeIcon } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

type DirEntry = { name: string; path: string };

type ListDirResult = {
  path?: string;
  parent?: string;
  entries?: Array<{ name: string; path: string; is_dir?: boolean }>;
};

/** Browse the agent host's folders and pick the workspace for a new chat. */
export function WorkspacePicker({
  client,
  initialPath,
  onSelect,
  onClose,
}: {
  client: RPCClient;
  initialPath?: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [currentPath, setCurrentPath] = useState<string | null>(null);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typedPath, setTypedPath] = useState<string | null>(null);

  const load = useCallback(
    async (path?: string | null) => {
      setLoading(true);
      setError(null);
      try {
        const res = (await client.serverCall('fs_list_dir', {
          path: path || undefined,
          include_files: false,
          ignore_hidden: true,
        })) as ListDirResult;
        setCurrentPath(res.path || null);
        setParentPath(res.parent || null);
        setEntries(
          (res.entries ?? [])
            .filter((entry) => entry.is_dir !== false)
            .map((entry) => ({ name: entry.name, path: entry.path }))
        );
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setLoading(false);
      }
    },
    [client]
  );

  const home = useCallback(async () => {
    try {
      const res = (await client.serverCall('fs_get_home')) as { path?: string };
      return res.path;
    } catch {
      return undefined;
    }
  }, [client]);

  useEffect(() => {
    void (async () => {
      let start = initialPath;
      if (!start) {
        try {
          // The agent's workspace root, not the server's own working directory.
          const res = (await client.serverCall('fs_get_workspace_root')) as { path?: string };
          start = res.path;
        } catch {}
      }
      await load(start ?? (await home()));
    })();
  }, [client, initialPath, load, home]);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[80vh] flex-col gap-3 sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Choose workspace</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Parent folder"
            disabled={!parentPath}
            onClick={() => parentPath && void load(parentPath)}
          >
            <ArrowUpIcon />
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label="Home folder" onClick={() => void home().then(load)}>
            <HomeIcon />
          </Button>
          <form
            className="min-w-0 flex-1"
            onSubmit={(event) => {
              event.preventDefault();
              if (typedPath?.trim()) {
                void load(typedPath.trim());
              }
              setTypedPath(null);
            }}
          >
            <Input
              aria-label="Folder path"
              value={typedPath ?? currentPath ?? ''}
              onChange={(event) => setTypedPath(event.target.value)}
              onBlur={() => setTypedPath(null)}
              className="h-8 text-xs"
            />
          </form>
        </div>
        <div className="min-h-32 flex-1 overflow-y-auto rounded-md border">
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : error ? (
            <p className="px-4 py-6 text-center text-sm text-destructive">{error}</p>
          ) : entries.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">No subfolders</p>
          ) : (
            <ul className="py-1">
              {entries.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    onClick={() => void load(entry.path)}
                    className="flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground"
                  >
                    <FolderIcon className="shrink-0 text-muted-foreground" />
                    <span className="truncate">{entry.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!currentPath} onClick={() => currentPath && onSelect(currentPath)}>
            Use this folder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
