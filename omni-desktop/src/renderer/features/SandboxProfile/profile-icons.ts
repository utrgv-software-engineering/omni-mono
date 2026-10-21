/**
 * Per-profile icons for the sandbox pickers and composer pill. Kept
 * store-free (unlike `profile-list.ts`, which pulls in the discovery atoms) so omniagents-ui components can import it directly.
 *
 * The icon answers "what kind of computer is this?" — the same question
 * the display name answers. Risk is a separate accent: unsandboxed
 * profiles keep their identity icon and get a `text-warning` tint via
 * `isUnsandboxedProfile`, never a different glyph.
 */
import { Box, Laptop, type LucideIcon, Monitor } from 'lucide-react';

const PROFILE_ICONS: Record<string, LucideIcon> = {
  host: Laptop,
  devbox: Monitor,
  wasmbox: Box,
};

// User-created profiles get the generic sandbox glyph.
export const getProfileIcon = (name: string): LucideIcon => PROFILE_ICONS[name] ?? Box;

/**
 * True for profiles that execute directly on a real machine with no
 * containment boundary — `host`. Consumers render the icon with a warning
 * tint; the description text still carries the words.
 */
export const isUnsandboxedProfile = (name: string): boolean => name === 'host';
