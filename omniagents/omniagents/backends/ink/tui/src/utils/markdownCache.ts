/**
 * LRU cache for markdown rendering
 * Prevents re-rendering the same markdown content multiple times
 */

import { renderMarkdown } from './markdown.js';

const cache = new Map<string, string>();
const MAX_CACHE_SIZE = 100;

/**
 * Render markdown with caching
 * Uses LRU (Least Recently Used) eviction policy
 */
export function renderMarkdownCached(content: string): string {
  // Check cache first
  if (cache.has(content)) {
    const cached = cache.get(content)!;

    // Move to end (LRU)
    cache.delete(content);
    cache.set(content, cached);

    return cached;
  }

  // Render and cache
  const rendered = renderMarkdown(content);

  // Evict oldest if at capacity
  if (cache.size >= MAX_CACHE_SIZE) {
    const firstKey = cache.keys().next().value;
    if (firstKey !== undefined) {
      cache.delete(firstKey);
    }
  }

  cache.set(content, rendered);
  return rendered;
}

/**
 * Clear the cache (useful for testing)
 */
export function clearMarkdownCache(): void {
  cache.clear();
}

/**
 * Get cache statistics (for debugging)
 */
export function getMarkdownCacheStats() {
  return {
    size: cache.size,
    maxSize: MAX_CACHE_SIZE,
  };
}
