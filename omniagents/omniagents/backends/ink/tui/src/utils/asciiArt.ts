import { Theme } from "../theme.js";
/**
 * ASCII art generator - creates block-letter art from text
 * Matches Bubbletea implementation
 */

const ASCII_CHARS: Record<string, [string, string, string]> = {
  " ": ["   ", "   ", "   "],
  "0": ["█▀█ ", "█ █ ", "▀▀▀ "],
  "1": ["▄█ ", " █ ", " ▀ "],
  "2": ["▀▀█ ", "█▀▀ ", "▀▀▀ "],
  "3": ["▀▀█ ", "▀▀█ ", "▀▀▀ "],
  "4": ["█ █ ", "▀▀█ ", "  ▀ "],
  "5": ["█▀▀ ", "▀▀█ ", "▀▀▀ "],
  "6": ["█   ", "█▀█ ", "▀▀▀ "],
  "7": ["▀▀█ ", " █▀ ", " ▀  "],
  "8": ["█▀█ ", "█▀█ ", "▀▀▀ "],
  "9": ["█▀█ ", "▀▀█ ", "▀▀▀ "],
  A: ["█▀█ ", "█▀█ ", "▀ ▀ "],
  B: ["█▀▄ ", "█▀▄ ", "▀▀  "],
  C: ["█▀▀ ", "█   ", "▀▀▀ "],
  D: ["█▀▄ ", "█ █ ", "▀▀  "],
  E: ["█▀▀ ", "█▀▀ ", "▀▀▀ "],
  F: ["█▀▀ ", "█▀  ", "▀   "],
  G: ["█▀▀ ", "█ █ ", "▀▀▀ "],
  H: ["█ █ ", "█▀█ ", "▀ ▀ "],
  I: ["█ ", "█ ", "▀ "],
  J: ["  █ ", "▄ █ ", "▀▀▀ "],
  K: ["█ █ ", "█▀▄ ", "▀ ▀ "],
  L: ["█   ", "█   ", "▀▀▀ "],
  M: ["█▀▄▀█ ", "█ ▀ █ ", "▀   ▀ "],
  N: ["█▄ █ ", "█ ▀█ ", "▀  ▀ "],
  O: ["█▀█ ", "█ █ ", "▀▀▀ "],
  P: ["█▀█ ", "█▀▀ ", "▀   "],
  Q: ["█▀█ ", "█ █ ", "▀▀█ "],
  R: ["█▀█ ", "██▄ ", "▀ ▀ "],
  S: ["█▀▀ ", "▀▀█ ", "▀▀▀ "],
  T: ["▀█▀ ", " █  ", " ▀  "],
  U: ["█ █ ", "█ █ ", "▀▀▀ "],
  V: ["█ █ ", "█ █ ", " ▀  "],
  W: ["█ █ █ ", "█ █ █ ", "▀▀▀▀▀ "],
  X: ["█ █ ", "▄▀▄ ", "▀ ▀ "],
  Y: ["█ █ ", "▀█▀ ", " ▀  "],
  Z: ["▀▀█ ", "▄▀  ", "▀▀▀ "],
};

/**
 * Creates 3-line ASCII art from text
 */
export function createASCIIArt(text: string): string[] {
  const upper = text.toUpperCase();
  let line1 = "";
  let line2 = "";
  let line3 = "";

  for (const char of upper) {
    const pattern = ASCII_CHARS[char];
    if (pattern) {
      line1 += pattern[0];
      line2 += pattern[1];
      line3 += pattern[2];
    } else {
      // For any other character, use the character itself
      line1 += char + "  ";
      line2 += "   ";
      line3 += "   ";
    }
  }

  return [line1.trimEnd(), line2.trimEnd(), line3.trimEnd()];
}

/**
 * Simple hex to RGB converter
 */
function hexToRgb(hex: string): [number, number, number] {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return result
    ? [
        parseInt(result[1], 16),
        parseInt(result[2], 16),
        parseInt(result[3], 16),
      ]
    : [0, 0, 0];
}

/**
 * RGB to hex converter
 */
function rgbToHex(r: number, g: number, b: number): string {
  return (
    "#" +
    [r, g, b]
      .map((x) => {
        const hex = Math.round(x).toString(16);
        return hex.length === 1 ? "0" + hex : hex;
      })
      .join("")
  );
}

/**
 * Linear interpolation between two colors
 */
function interpolateColor(
  color1: string,
  color2: string,
  factor: number,
): string {
  const [r1, g1, b1] = hexToRgb(color1);
  const [r2, g2, b2] = hexToRgb(color2);

  const r = r1 + factor * (r2 - r1);
  const g = g1 + factor * (g2 - g1);
  const b = b1 + factor * (b2 - b1);

  return rgbToHex(r, g, b);
}

/**
 * Applies horizontal gradient to ASCII art
 * Returns array of colored strings for each line
 */
export function applyGradient(
  lines: string[],
  startColor: string = Theme.accentUser,
  endColor: string = Theme.accentAgent,
): string[] {
  return lines.map((line) => {
    let result = "";
    const chars = Array.from(line);

    for (let i = 0; i < chars.length; i++) {
      const char = chars[i];

      // Skip spaces - don't color them
      if (char === " ") {
        result += char;
        continue;
      }

      // Calculate position in gradient (0.0 to 1.0)
      const position = chars.length === 1 ? 0.5 : i / (chars.length - 1);
      const color = interpolateColor(startColor, endColor, position);

      // Use ANSI escape codes for coloring
      result += `\x1b[38;2;${hexToRgb(color).join(";")}m${char}\x1b[0m`;
    }

    return result;
  });
}
