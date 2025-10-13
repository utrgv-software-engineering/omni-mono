import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from omniagents.core.paths import get_traces_dir

class SQLiteStorage:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            raise ValueError("db_path must be provided")
        
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()  # Global lock for thread safety
        self._create_tables()
    
    @property
    def conn(self) -> sqlite3.Connection:
        """Thread-local connection"""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(str(self.db_path))
            self._local.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn
    
    def _create_tables(self):
        """Create tables if they don't exist"""
        with self.conn:
            self.conn.executescript('''
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    workflow_name TEXT,
                    group_id TEXT,
                    metadata JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_id TEXT,
                    span_type TEXT NOT NULL,
                    span_name TEXT,
                    data JSON NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    error JSON,
                    sequence_number INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_spans_trace 
                ON spans(trace_id, started_at);
                
                CREATE INDEX IF NOT EXISTS idx_spans_parent 
                ON spans(parent_id);
                
                CREATE INDEX IF NOT EXISTS idx_traces_created 
                ON traces(created_at DESC);
                
                CREATE INDEX IF NOT EXISTS idx_traces_group 
                ON traces(group_id);
                
                CREATE INDEX IF NOT EXISTS idx_spans_sequence
                ON spans(trace_id, sequence_number);
                
                CREATE TABLE IF NOT EXISTS analysis (
                    group_id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    marked_for_analysis BOOLEAN DEFAULT FALSE,
                    judgment TEXT CHECK(judgment IN ('acceptable', 'unacceptable') OR judgment IS NULL),
                    notes TEXT DEFAULT '',
                    analyzed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_analysis_project
                ON analysis(project);
                
                CREATE INDEX IF NOT EXISTS idx_analysis_judgment
                ON analysis(project, judgment);
                
                CREATE TABLE IF NOT EXISTS failure_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(project, name)
                );
                
                CREATE INDEX IF NOT EXISTS idx_categories_project
                ON failure_categories(project);
                
                CREATE TABLE IF NOT EXISTS analysis_categories (
                    group_id TEXT NOT NULL,
                    category_id INTEGER NOT NULL,
                    project TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, category_id),
                    FOREIGN KEY (group_id) REFERENCES analysis(group_id) ON DELETE CASCADE,
                    FOREIGN KEY (category_id) REFERENCES failure_categories(id) ON DELETE CASCADE
                );
                
                CREATE INDEX IF NOT EXISTS idx_analysis_categories_group
                ON analysis_categories(group_id);
                
                CREATE INDEX IF NOT EXISTS idx_analysis_categories_category
                ON analysis_categories(category_id);
            ''')
    
    def insert_trace(self, trace_data: Dict[str, Any]):
        """Insert a new trace"""
        with self.conn:
            self.conn.execute('''
                INSERT OR IGNORE INTO traces 
                (trace_id, workflow_name, group_id, metadata)
                VALUES (?, ?, ?, ?)
            ''', (
                trace_data['id'],
                trace_data.get('workflow_name'),
                trace_data.get('group_id'),
                json.dumps(trace_data.get('metadata', {}))
            ))
    
    def insert_span(self, span_data: Dict[str, Any]):
        """Insert a span with auto-incrementing sequence number"""
        # Extract span type and name from span_data
        span_info = span_data.get('span_data', {})
        span_type = span_info.get('type', 'unknown')
        span_name = span_info.get('name', '')
        
        with self._lock:  # Thread safety for sequence number
            with self.conn:
                # Get next sequence number for this trace
                cursor = self.conn.execute(
                    "SELECT MAX(sequence_number) FROM spans WHERE trace_id = ?",
                    (span_data['trace_id'],)
                )
                result = cursor.fetchone()
                seq_num = 0 if result[0] is None else result[0] + 1
                
                self.conn.execute('''
                    INSERT OR IGNORE INTO spans
                    (span_id, trace_id, parent_id, span_type, span_name, 
                     data, started_at, ended_at, error, sequence_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    span_data['id'],
                    span_data['trace_id'],
                    span_data.get('parent_id'),
                    span_type,
                    span_name,
                    json.dumps(span_data),
                    span_data.get('started_at'),
                    span_data.get('ended_at'),
                    json.dumps(span_data.get('error')) if span_data.get('error') else None,
                    seq_num
                ))
    
    def bulk_insert_spans(self, spans_list: List[Dict[str, Any]]):
        """Bulk insert multiple spans efficiently"""
        if not spans_list:
            return
        
        with self._lock:
            with self.conn:
                # Get starting sequence number
                trace_id = spans_list[0]['trace_id']
                cursor = self.conn.execute(
                    "SELECT MAX(sequence_number) FROM spans WHERE trace_id = ?",
                    (trace_id,)
                )
                result = cursor.fetchone()
                seq_num = 0 if result[0] is None else result[0] + 1
                
                data = []
                for i, span_data in enumerate(spans_list):
                    span_info = span_data.get('span_data', {})
                    data.append((
                        span_data['id'],
                        span_data['trace_id'],
                        span_data.get('parent_id'),
                        span_info.get('type', 'unknown'),
                        span_info.get('name', ''),
                        json.dumps(span_data),
                        span_data.get('started_at'),
                        span_data.get('ended_at'),
                        json.dumps(span_data.get('error')) if span_data.get('error') else None,
                        seq_num + i
                    ))
                
                self.conn.executemany('''
                    INSERT OR IGNORE INTO spans
                    (span_id, trace_id, parent_id, span_type, span_name,
                     data, started_at, ended_at, error, sequence_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', data)
    
    def get_traces(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent traces from this database

        Args:
            limit: Maximum number of traces to return
        """
        cursor = self.conn.execute('''
            SELECT trace_id, workflow_name, group_id, metadata,
                   datetime(created_at) || 'Z' as created_at
            FROM traces 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor]

    def get_traces_by_group(self, group_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.execute('''
            SELECT trace_id, workflow_name, group_id, metadata,
                   datetime(created_at) || 'Z' as created_at
            FROM traces
            WHERE group_id = ?
            ORDER BY created_at DESC
        ''', (group_id,))
        return [dict(row) for row in cursor]

    def get_workflow_names(self) -> List[str]:
        """Get distinct workflow names present in traces"""
        cursor = self.conn.execute('''
            SELECT DISTINCT workflow_name
            FROM traces
            WHERE workflow_name IS NOT NULL AND workflow_name != ''
            ORDER BY workflow_name COLLATE NOCASE
        ''')
        return [row['workflow_name'] for row in cursor]
    
    @staticmethod
    def get_projects() -> List[str]:
        """Get list of all available project databases"""
        storage_dir = get_traces_dir()
        if not storage_dir.exists():
            return []
        
        # Find all .db files and return their stems (project names)
        db_files = storage_dir.glob("*.db")
        return sorted([db.stem for db in db_files])
    
    def get_spans_for_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Get all spans for a trace"""
        cursor = self.conn.execute('''
            SELECT * FROM spans 
            WHERE trace_id = ?
            ORDER BY sequence_number
        ''', (trace_id,))
        return [dict(row) for row in cursor]
    
    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific trace by ID"""
        cursor = self.conn.execute('''
            SELECT * FROM traces 
            WHERE trace_id = ?
        ''', (trace_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def get_analysis(self, project: str, group_id: str) -> Optional[Dict[str, Any]]:
        """Get analysis data for a specific conversation"""
        cursor = self.conn.execute('''
            SELECT * FROM analysis 
            WHERE project = ? AND group_id = ?
        ''', (project, group_id))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def get_all_analysis(self, project: str) -> List[Dict[str, Any]]:
        """Get all analysis data for a project"""
        cursor = self.conn.execute('''
            SELECT * FROM analysis 
            WHERE project = ?
            ORDER BY updated_at DESC
        ''', (project,))
        return [dict(row) for row in cursor]
    
    def upsert_analysis(self, project: str, group_id: str, analysis_data: Dict[str, Any]):
        """Insert or update analysis data for a conversation"""
        with self.conn:
            # Check if analysis exists
            cursor = self.conn.execute(
                "SELECT 1 FROM analysis WHERE group_id = ?",
                (group_id,)
            )
            exists = cursor.fetchone() is not None
            
            if exists:
                # Update existing
                self.conn.execute('''
                    UPDATE analysis SET
                        marked_for_analysis = ?,
                        judgment = ?,
                        notes = ?,
                        analyzed_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE group_id = ?
                ''', (
                    analysis_data.get('markedForAnalysis', False),
                    analysis_data.get('judgment'),
                    analysis_data.get('notes', ''),
                    analysis_data.get('analyzedAt'),
                    group_id
                ))
            else:
                # Insert new
                self.conn.execute('''
                    INSERT INTO analysis
                    (group_id, project, marked_for_analysis, judgment, notes, analyzed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    group_id,
                    project,
                    analysis_data.get('markedForAnalysis', False),
                    analysis_data.get('judgment'),
                    analysis_data.get('notes', ''),
                    analysis_data.get('analyzedAt')
                ))
    
    def delete_analysis(self, group_id: str):
        """Delete analysis data for a conversation"""
        with self.conn:
            self.conn.execute('DELETE FROM analysis WHERE group_id = ?', (group_id,))
    
    def get_failure_categories(self, project: str) -> List[Dict[str, Any]]:
        """Get all failure categories for a project"""
        cursor = self.conn.execute('''
            SELECT id, name, description, created_at
            FROM failure_categories
            WHERE project = ?
            ORDER BY name
        ''', (project,))
        return [dict(row) for row in cursor]
    
    def create_failure_category(self, project: str, name: str, description: Optional[str] = None) -> int:
        """Create a new failure category for a project"""
        with self.conn:
            cursor = self.conn.execute('''
                INSERT INTO failure_categories (project, name, description)
                VALUES (?, ?, ?)
            ''', (project, name, description))
            return cursor.lastrowid
    
    def get_analysis_categories(self, group_id: str) -> List[int]:
        """Get category IDs associated with an analysis"""
        cursor = self.conn.execute('''
            SELECT category_id
            FROM analysis_categories
            WHERE group_id = ?
        ''', (group_id,))
        return [row['category_id'] for row in cursor]
    
    def set_analysis_categories(self, project: str, group_id: str, category_ids: List[int]):
        """Set categories for an analysis (replaces existing)"""
        with self.conn:
            # Delete existing categories
            self.conn.execute('DELETE FROM analysis_categories WHERE group_id = ?', (group_id,))
            
            # Insert new categories
            if category_ids:
                data = [(group_id, cat_id, project) for cat_id in category_ids]
                self.conn.executemany('''
                    INSERT INTO analysis_categories (group_id, category_id, project)
                    VALUES (?, ?, ?)
                ''', data)
    
    def get_category_usage_stats(self, project: str) -> Dict[int, int]:
        """Get usage count for each category in a project"""
        cursor = self.conn.execute('''
            SELECT fc.id, COUNT(ac.group_id) as usage_count
            FROM failure_categories fc
            LEFT JOIN analysis_categories ac ON fc.id = ac.category_id
            WHERE fc.project = ?
            GROUP BY fc.id
        ''', (project,))
        return {row['id']: row['usage_count'] for row in cursor}
    
    def delete_failure_category(self, project: str, category_id: int) -> bool:
        """Delete a failure category. Returns True if deleted, False if not found"""
        with self.conn:
            # Check if category exists and belongs to this project
            cursor = self.conn.execute('''
                SELECT id FROM failure_categories 
                WHERE id = ? AND project = ?
            ''', (category_id, project))
            
            if not cursor.fetchone():
                return False
            
            # Delete the category (CASCADE will handle analysis_categories)
            self.conn.execute('''
                DELETE FROM failure_categories 
                WHERE id = ? AND project = ?
            ''', (category_id, project))
            
            return True
    
    def get_category_usage(self, project: str, category_id: int) -> int:
        """Get usage count for a specific category"""
        cursor = self.conn.execute('''
            SELECT COUNT(*) as usage_count
            FROM analysis_categories ac
            JOIN failure_categories fc ON fc.id = ac.category_id
            WHERE fc.id = ? AND fc.project = ?
        ''', (category_id, project))
        row = cursor.fetchone()
        return row['usage_count'] if row else 0
    
    def close(self):
        """Close connection"""
        if hasattr(self._local, 'conn'):
            self._local.conn.close()
            delattr(self._local, 'conn')
