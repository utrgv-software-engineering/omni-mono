/**
 * Debug logging utility that writes to a file
 */

import { appendFileSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const DEBUG_LOG_PATH = join(homedir(), ".omni-ink-debug.log");

// Use object to ensure reference is shared across module boundaries
const state = { enabled: false };

/**
 * Enable debug logging to file
 */
export function enableDebugLog() {
  state.enabled = true;
  // Write header
  const header = `\n\n=== Debug Session Started: ${new Date().toISOString()} ===\n`;
  try {
    appendFileSync(DEBUG_LOG_PATH, header, "utf8");
    console.error(`[DEBUG] Log file created: ${DEBUG_LOG_PATH}`);
  } catch (err) {
    console.error("Failed to write debug log:", err);
  }
}

/**
 * Write a debug message to the log file
 */
export function debugLog(message: string) {
  if (!state.enabled) return;

  const timestamp = new Date().toISOString();
  const logLine = `[${timestamp}] ${message}\n`;

  try {
    appendFileSync(DEBUG_LOG_PATH, logLine, "utf8");
  } catch (err) {
    console.error(`Failed to append to debug log: ${err}`);
  }
}

/**
 * Get the debug log file path
 */
export function getDebugLogPath(): string {
  return DEBUG_LOG_PATH;
}
