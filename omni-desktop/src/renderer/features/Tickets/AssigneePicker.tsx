import { useStore } from '@nanostores/react';
import { Bot, User } from 'lucide-react';
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
import { ticketApi } from '@/renderer/features/Tickets/state';
import { persistedStoreApi } from '@/renderer/services/store';
import type { TicketId } from '@/shared/types';

/**
 * Assign a ticket to a resident agent, or leave it Unassigned (the default).
 * This sets the optional `assignee` pointer; for residents (`agent:<id>`) it
 * also triggers the assignment wakeup.
 */
export const AssigneePicker = memo(function AssigneePicker({
  ticketId,
  assignee,
}: {
  ticketId: TicketId;
  assignee?: string;
}) {
  const residents = useStore(persistedStoreApi.$atom).residentAgents.filter((a) => a.enabled);

  const handleAssign = useCallback(
    (principalId: string) => void ticketApi.assignTicket(ticketId, principalId || null),
    [ticketId]
  );

  const currentResident = residents.find((a) => residentPrincipalId(a.id) === assignee);
  const label = currentResident ? currentResident.name : (assignee ?? 'Unassigned');

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="ghost" aria-label="Assign ticket">
          {currentResident ? <Bot /> : <User />}
          {label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuRadioGroup value={assignee ?? ''} onValueChange={handleAssign}>
          <DropdownMenuRadioItem value="">Unassigned</DropdownMenuRadioItem>
          {residents.length > 0 && <DropdownMenuSeparator />}
          {residents.map((a) => (
            <DropdownMenuRadioItem key={a.id} value={residentPrincipalId(a.id)}>
              <Bot />
              {a.name}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
});
