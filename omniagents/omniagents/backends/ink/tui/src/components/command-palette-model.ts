// Presentation logic for the slash-command palette, kept out of the
// component so it is unit-testable without an Ink render (same split as
// ./model-picker-model).
//
// The palette answers a question the TUI could not answer before: what
// can I type after a slash? ``handleSlashCommand`` already calls
// ``list_server_functions`` — but only to decide whether the name the
// user *already guessed* exists, and a validator is not a directory.
// These rows are that directory, builtins included, so the menu is the
// documentation the same way the ctrl+a which-key popup is.

export type CommandSource = "builtin" | "server";

export type CommandRow = {
  /** Command name WITHOUT the leading slash. */
  name: string;
  description: string | null;
  source: CommandSource;
  /** True when the server function declares required parameters. */
  takesArgs: boolean;
};

/** A server function as ``list_server_functions`` reports it. */
export type ServerFunction = {
  name: string;
  description?: string;
  params_schema?: Record<string, unknown>;
  result_schema?: Record<string, unknown>;
};

/**
 * The client-side commands. These never reach the server, so they
 * cannot come back from ``list_server_functions`` — listing them here
 * is what makes the palette the whole story rather than the
 * server-side half of it.
 */
export const BUILTIN_COMMANDS: readonly CommandRow[] = [
  {
    name: "model",
    description: "Switch the session's model",
    source: "builtin",
    takesArgs: false,
  },
  {
    name: "help",
    description: "Show this command palette",
    source: "builtin",
    takesArgs: false,
  },
  {
    name: "exit",
    description: "Quit the TUI",
    source: "builtin",
    takesArgs: false,
  },
] as const;

/**
 * True when the function's parameter schema declares anything the
 * caller must supply. Used only to append a `<args>` hint, so an
 * unparseable schema degrades to "no hint" rather than a wrong one.
 */
export function declaresRequiredParams(
  schema: Record<string, unknown> | undefined,
): boolean {
  if (!schema || typeof schema !== "object") return false;
  const required = schema.required;
  if (Array.isArray(required) && required.length > 0) return true;
  // Some servers describe a single positional payload without a
  // `required` list; a non-empty `properties` map is the next best
  // signal that the function expects input.
  const properties = schema.properties;
  return (
    typeof properties === "object" &&
    properties !== null &&
    Object.keys(properties as Record<string, unknown>).length > 0
  );
}

/**
 * Builtins first, then server functions alphabetically. A server
 * function whose name collides with a builtin is dropped: the builtin
 * is what `handleSlashCommand` will actually run, and listing a row
 * that does something other than what it says is worse than listing
 * nothing.
 */
export function buildCommandRows(
  serverFunctions: readonly ServerFunction[],
  builtins: readonly CommandRow[] = BUILTIN_COMMANDS,
): CommandRow[] {
  const reserved = new Set(builtins.map((b) => b.name.toLowerCase()));
  const server = serverFunctions
    .filter((fn) => typeof fn?.name === "string" && fn.name !== "")
    .filter((fn) => !reserved.has(fn.name.toLowerCase()))
    .map<CommandRow>((fn) => ({
      name: fn.name,
      description:
        typeof fn.description === "string" && fn.description.trim() !== ""
          ? fn.description.trim()
          : null,
      source: "server",
      takesArgs: declaresRequiredParams(fn.params_schema),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
  return [...builtins, ...server];
}

/**
 * Filter by the text the user has typed after the slash. Prefix matches
 * rank above substring matches, and each group keeps the input order
 * (builtins first) so the top row is the one a user typing `/mo` means.
 */
export function filterCommands(
  rows: readonly CommandRow[],
  query: string,
): CommandRow[] {
  const needle = query.trim().toLowerCase().replace(/^\//, "");
  if (needle === "") return [...rows];
  const prefix: CommandRow[] = [];
  const substring: CommandRow[] = [];
  for (const row of rows) {
    const name = row.name.toLowerCase();
    if (name.startsWith(needle)) {
      prefix.push(row);
    } else if (
      name.includes(needle) ||
      (row.description ?? "").toLowerCase().includes(needle)
    ) {
      substring.push(row);
    }
  }
  return [...prefix, ...substring];
}

/**
 * The text the composer receives when a row is chosen. Commands that
 * take arguments keep the trailing space so the cursor lands where the
 * argument goes; commands that do not are complete as typed.
 */
export function completionFor(row: CommandRow): string {
  return row.takesArgs ? `/${row.name} ` : `/${row.name}`;
}
