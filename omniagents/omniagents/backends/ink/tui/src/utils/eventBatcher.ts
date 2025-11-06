/**
 * Event batching system to prevent render cascades
 * Collects all synchronous updates and applies them in one batch
 */

export class EventBatcher {
  private queue: Array<() => void> = [];
  private scheduled = false;

  /**
   * Add an update function to the batch queue
   */
  add(fn: () => void): void {
    this.queue.push(fn);
    this.schedule();
  }

  /**
   * Schedule batch flush using microtask queue
   * All synchronous events in current tick will be batched
   */
  private schedule(): void {
    if (this.scheduled) return;
    this.scheduled = true;

    // Use queueMicrotask to batch all synchronous events
    // This runs after current JavaScript execution completes
    // but before any rendering
    queueMicrotask(() => {
      this.flush();
    });
  }

  /**
   * Execute all queued updates in one batch
   */
  private flush(): void {
    const fns = this.queue.splice(0);
    this.scheduled = false;

    // Execute all updates - Zustand will batch these internally
    fns.forEach(fn => fn());
  }

  /**
   * Get current queue size (for debugging)
   */
  get size(): number {
    return this.queue.length;
  }
}
