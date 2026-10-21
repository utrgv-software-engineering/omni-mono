// Connection & migrations
export { closeDatabase, openDatabase } from './connection.js';
export { runMigrations } from './migrate.js';
export type { Migration } from './schema.js';
export { migrations } from './schema.js';
export { tx } from './tx.js';

// Repository — sync SQLite core + async contract/adapter
export { ProjectsRepo } from './repo.js';
export type { ColumnSyncInput, ColumnSyncResult, IProjectsRepo, TicketRemap } from './repo-interface.js';
export { SqliteProjectsRepo } from './sqlite-repo.js';

// Types
export type {
  ColumnRow,
  CommentRow,
  HandbookRow,
  InboxRow,
  MilestoneRow,
  PageRow,
  ProjectRow,
  ResidentAlarmRow,
  ResidentChannelRow,
  ResidentMemoryRow,
  ResidentMessageRow,
  ResidentProtocolRow,
  ResidentRow,
  TaskRow,
  TicketRow,
} from './types.js';

// IDs
export { columnId, commentId, inboxId, milestoneId, pageId, projectId, residentId, taskId, ticketId } from './ids.js';

// Timestamps
export { fromIso, nowTimestamp, toIso } from './timestamps.js';

// Defaults
export type { ColumnCategory, ColumnDef } from './defaults.js';
export { DEFAULT_COLUMNS, defaultColumnId, logicalColumnId, positionalCategory, SIMPLE_COLUMNS } from './defaults.js';

// Page filesystem
export {
  deletePageContent,
  deleteProjectPages,
  getPageDir,
  getPagePath,
  readPageContent,
  writePageContent,
} from './pages-fs.js';

// Markdown normalization
export { normalizeMarkdown } from './normalize-markdown.js';

// JSON migration
export type { JsonStoreData } from './migrate-from-json.js';
export { migrateFromJson } from './migrate-from-json.js';

// Path resolution (shared between launcher and MCP server)
export { getDefaultDbPath, getDefaultPagesDir, getOmniConfigDir } from './paths.js';
