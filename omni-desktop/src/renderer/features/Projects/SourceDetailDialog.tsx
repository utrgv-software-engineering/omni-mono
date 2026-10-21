import { memo } from 'react';

import { cn } from '@/renderer/ds/cn';
import { Button } from '@/renderer/ds/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/renderer/ds/ui/dialog';
import type { ProjectSource } from '@/shared/types';

type SourceDetailDialogProps = {
  open: boolean;
  onClose: () => void;
  source: ProjectSource;
  onEdit: () => void;
  onRemove: () => void;
};

const sourceLocation = (source: ProjectSource): string =>
  source.kind === 'local' ? source.workspaceDir : source.repoUrl;

export const SourceDetailDialog = memo(({ open, onClose, source, onEdit, onRemove }: SourceDetailDialogProps) => {
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{source.mountName}</DialogTitle>
        </DialogHeader>
        <div className={cn('min-h-0 overflow-y-auto', 'flex flex-col gap-4')}>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Source</span>
            <div className="flex flex-col gap-2 border border-border rounded-lg bg-card p-4">
              <div className="flex items-baseline gap-4">
                <span className="w-24 shrink-0 text-xs text-muted-foreground">Kind</span>
                <span className="min-w-0 flex-1 wrap-anywhere text-sm text-foreground">{source.kind}</span>
              </div>
              <div className="flex items-baseline gap-4">
                <span className="w-24 shrink-0 text-xs text-muted-foreground">Location</span>
                <span className="min-w-0 flex-1 wrap-anywhere text-sm text-foreground">{sourceLocation(source)}</span>
              </div>
              <div className="flex items-baseline gap-4">
                <span className="w-24 shrink-0 text-xs text-muted-foreground">Mount</span>
                <span className="min-w-0 flex-1 wrap-anywhere text-sm text-foreground">
                  /workspace/{source.mountName}
                </span>
              </div>
              {source.kind === 'git-remote' && source.defaultBranch && (
                <div className="flex items-baseline gap-4">
                  <span className="w-24 shrink-0 text-xs text-muted-foreground">Branch</span>
                  <span className="min-w-0 flex-1 wrap-anywhere text-sm text-foreground">{source.defaultBranch}</span>
                </div>
              )}
            </div>
          </div>
        </div>
        <DialogFooter className="flex justify-between">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onEdit}>
              Edit
            </Button>
            <Button variant="destructive" onClick={onRemove}>
              Remove
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});
SourceDetailDialog.displayName = 'SourceDetailDialog';
