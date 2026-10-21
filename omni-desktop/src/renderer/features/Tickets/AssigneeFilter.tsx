import { useStore } from '@nanostores/react';
import { Filter } from 'lucide-react';
import { memo, useCallback } from 'react';

import { residentPrincipalId } from '@/lib/resident-agent';
import { Button } from '@/renderer/ds/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/renderer/ds/ui/dropdown-menu';
import { $assigneeFilter } from '@/renderer/features/Tickets/state';
import { persistedStoreApi } from '@/renderer/services/store';

/**
 * Filter the board by assignee (resident agents). Hidden when the roster is
 * empty; purely a view filter.
 */
export const AssigneeFilter = memo(function AssigneeFilter() {
  const filter = useStore($assigneeFilter);
  const residents = useStore(persistedStoreApi.$atom).residentAgents.filter((a) => a.enabled);

  const handleCheckedChange = useCallback((value: string) => $assigneeFilter.set(value), []);

  // Nobody to filter by.
  if (residents.length === 0) {
    return null;
  }

  const label =
    filter === 'all'
      ? 'All assignees'
      : filter === 'unassigned'
        ? 'Unassigned'
        : (residents.find((a) => residentPrincipalId(a.id) === filter)?.name ?? 'Assignee');

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="ghost">
          <Filter />
          {label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuRadioGroup value={filter} onValueChange={handleCheckedChange}>
          <DropdownMenuRadioItem value="all">All assignees</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="unassigned">Unassigned</DropdownMenuRadioItem>
          <DropdownMenuSeparator />
          {residents.map((a) => (
            <DropdownMenuRadioItem key={a.id} value={residentPrincipalId(a.id)}>
              {a.name}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
});
