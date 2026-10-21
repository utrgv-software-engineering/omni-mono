import { describe, expect, it, vi } from 'vitest';

import { StringMatcher } from './string-matcher';

describe('StringMatcher', () => {
  it('should call onMatch when a match is found', () => {
    const onMatch = vi.fn();
    const filter = (_: string) => true;
    const re = /https:\/\/example\.com/;
    const matcher = new StringMatcher({ filter, onMatch, re });

    matcher.checkForMatch('Visit https://example.com for more info');

    expect(onMatch).toHaveBeenCalledWith('https://example.com');
    expect(matcher.hasMatched()).toBe(true);
  });

  it('should not call onMatch if no match is found', () => {
    const onMatch = vi.fn();
    const filter = (_: string) => true;
    const re = /https:\/\/example\.com/;
    const matcher = new StringMatcher({ filter, onMatch, re });

    matcher.checkForMatch('Visit https://another-site.com for more info');

    expect(onMatch).not.toHaveBeenCalled();
    expect(matcher.hasMatched()).toBe(false);
  });

  it('should not call onMatch if filter returns false', () => {
    const onMatch = vi.fn();
    const filter = (_: string) => false;
    const re = /https:\/\/example\.com/;
    const matcher = new StringMatcher({ filter, onMatch, re });

    matcher.checkForMatch('Visit https://example.com for more info');

    expect(onMatch).not.toHaveBeenCalled();
    expect(matcher.hasMatched()).toBe(false);
  });

  it('should reset matched state', () => {
    const onMatch = vi.fn();
    const filter = (_: string) => true;
    const re = /https:\/\/example\.com/;
    const matcher = new StringMatcher({ filter, onMatch, re });

    matcher.checkForMatch('Visit https://example.com for more info');
    expect(matcher.hasMatched()).toBe(true);

    matcher.reset();
    expect(matcher.hasMatched()).toBe(false);
  });
});
