import { Button } from '@omniagents/ui/button';
import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import { useStickToBottomContext } from 'use-stick-to-bottom';

/**
 * The row above a transcript that has older history. It loads the next page
 * when the reader scrolls up to it (or presses it), and keeps what they were
 * reading where it was while the older items go in above.
 */
export function EarlierHistory({
  state,
  onLoad,
  itemCount,
}: {
  state: 'more' | 'loading';
  onLoad: () => Promise<void>;
  itemCount: number;
}) {
  const { scrollRef } = useStickToBottomContext();
  const row = useRef<HTMLDivElement>(null);
  const fromBottom = useRef<number | null>(null);
  // Only a reader scrolling up loads more: a transcript opens at its top
  // before it settles at the newest message, and must not load on the way.
  const scrolledUp = useRef(false);

  const load = useCallback(() => {
    const element = scrollRef.current;
    if (element) {
      fromBottom.current = element.scrollHeight - element.scrollTop;
    }
    void onLoad().catch(() => {
      fromBottom.current = null;
    });
  }, [onLoad, scrollRef]);

  // The older items went in above: keep the same distance from the bottom.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element && fromBottom.current !== null) {
      element.scrollTop = element.scrollHeight - fromBottom.current;
      fromBottom.current = null;
    }
  }, [itemCount, scrollRef]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) {
      return;
    }
    let last = element.scrollTop;
    const onScroll = () => {
      if (element.scrollTop < last) {
        scrolledUp.current = true;
      }
      last = element.scrollTop;
    };
    element.addEventListener('scroll', onScroll, { passive: true });
    return () => element.removeEventListener('scroll', onScroll);
  }, [scrollRef]);

  useEffect(() => {
    const target = row.current;
    const root = scrollRef.current;
    if (!target || !root || state !== 'more' || typeof IntersectionObserver === 'undefined') {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (scrolledUp.current && entries.some((entry) => entry.isIntersecting)) {
          load();
        }
      },
      { root, rootMargin: '400px 0px 0px 0px' }
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [state, load, scrollRef]);

  return (
    // One height whatever it shows: what is above the reader must not move.
    <div ref={row} className="flex h-12 items-center justify-center" data-testid="earlier-history">
      {state === 'loading' ? (
        <span className="text-xs text-muted-foreground" role="status">
          Loading earlier messages…
        </span>
      ) : (
        <Button variant="ghost" size="sm" onClick={load}>
          Show earlier messages
        </Button>
      )}
    </div>
  );
}
