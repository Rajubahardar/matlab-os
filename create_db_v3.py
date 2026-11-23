"""SQLite Database Service for MATLAB OS (continued)"""
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)


class DatabaseService:
    """SQLite-based persistence service for MATLAB OS."""
    
    def __init__(self, db_path: str = "data/matlab_agent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self._create_tables()
        logger.info(f"Database service initialized at {self.db_path}")
    
    def _create_tables(self):
        """Create all required database tables."""
        cursor = self.conn.cursor()
        
        # Project memory table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                objectives TEXT,
                status TEXT DEFAULT 'pending',
                matlab_version TEXT,
                dependencies TEXT,
                next_steps TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Experiment registry table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                experiment_id TEXT,
                inputs TEXT,
                parameters TEXT,
                source_reference TEXT,
                status TEXT DEFAULT 'pending',
                outputs TEXT,
                errors TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        ''')
        
        # Evidence traceability table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id INTEGER,
                claim TEXT,
                observation TEXT,
                source TEXT,
                validated BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id)
            )
        ''')
        
        # Approval management table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                requested_by TEXT,
                approved BOOLEAN DEFAULT FALSE,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
    
    def get_experiments_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        """Get all experiments for a project."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM experiments WHERE project_id = ?', (project_id,))
        rows = cursor.fetchall()
        experiments = []
        for row in rows:
            experiments.append({
                'id': row[0], 'project_id': row[1], 'experiment_id': row[2],
                'inputs': json.loads(row[3]) if row[3] else None,
                'parameters': json.loads(row[4]) if row[4] else None,
                'source_reference': row[5], 'status': row[6],
                'outputs': row[7], 'errors': row[8],
                'timestamp': row[8]
            })
        return experiments
    
    def store_evidence(self, experiment_id: int, claim: str,
                       observation: str = None, source: str = None,
                       validated: bool = False) -> int:
        """Store evidence traceability entry."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO evidence (experiment_id, claim, observation, source, validated)
            VALUES (?, ?, ?, ?, ?)
        ''', (experiment_id, claim, observation, source, validated))
        self.conn.commit()
        return cursor.lastrowid
    
    def store_approval(self, operation: str, status: str = 'pending',
                       requested_by: str = None, reason: str = None) -> int:
        """Store an approval request."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO approvals (operation, status, requested_by, reason)
            VALUES (?, ?, ?, ?)
        ''', (operation, status, requested_by, reason))
        self.conn.commit()
        return cursor.lastrowid
    
    def approve_operation(self, approval_id: int, approved: bool = True,
                          reason: str = None) -> bool:
        """Approve or reject an operation."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE approvals SET approved = ?, reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (approved, reason, approval_id))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
        logger.info("Database service closed.")