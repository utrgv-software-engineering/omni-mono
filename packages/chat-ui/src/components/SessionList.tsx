import { formatRelativeTime, generateSessionTitle } from '@omniagents/chat-ui/lib/utils';
import { Button } from '@omniagents/ui/button';
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from '@omniagents/ui/item';
import React from 'react';

export type SessionItem = {
  id: string;
  created_at: string;
  archived: boolean;
  message_count: number;
  title?: string;
  pinned?: boolean;
  searchPreview?: string;
  first_message?: any;
  last_message?: any;
};

export function SessionList({ sessions, onSelect }: { sessions: SessionItem[]; onSelect: (id?: string) => void }) {
  return (
    <div className="px-3 py-3">
      <div className="text-sm text-muted-foreground mb-2">Resume a previous session or start a new one.</div>
      <ItemGroup className="gap-2">
        {sessions
          .filter((s) => s.message_count > 0)
          .map((s) => (
            <Item key={s.id} asChild variant="outline" size="sm">
              <Button
                type="button"
                variant="ghost"
                className="h-auto w-full justify-start whitespace-normal"
                onClick={() => onSelect(s.id)}
              >
                <ItemContent className="min-w-0 items-start text-left">
                  <ItemTitle className="max-w-full truncate">{generateSessionTitle(s)}</ItemTitle>
                  <ItemDescription>{formatRelativeTime(s.created_at)}</ItemDescription>
                </ItemContent>
              </Button>
            </Item>
          ))}
        <Button className="self-start" onClick={() => onSelect(undefined)}>
          Start New Session
        </Button>
      </ItemGroup>
    </div>
  );
}
