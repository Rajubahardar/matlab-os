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
    
    def store_project(self, name: str, objectives: str = None, 
                      matlab_version: str = None, dependencies: str = None,
                      next_steps: str = None) -> int:
        """Store a new project and return its ID."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO projects (name, objectives, matlab_version, dependencies, next_steps)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, objectives, matlab_version, dependencies, next_steps))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Get project by ID."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0], 'name': row[1], 'objectives': row[2],
                'status': row[3], 'matlab_version': row[4],
                'dependencies': row[5], 'next_steps': row[6],
                'created_at': row[7], 'updated_at': row[8]
            }
        return None
    
    def store_experiment(self, project_id: int, experiment_id: str,
                         inputs: Dict = None, parameters: Dict = None,
                         source_reference: str = None) -> int:
        """Store an experiment and return its ID."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO experiments (project_id, experiment_id, inputs, parameters, source_reference)
            VALUES (?, ?, ?, ?, ?)
        ''', (project_id, experiment_id, json.dumps(inputs) if inputs else None,
              json.dumps(parameters) if parameters else None, source_reference))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_experiment(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        """Get experiment by ID."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM experiments WHERE id = ?', (experiment_id,))
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0], 'project_id': row[1], 'experiment_id': row[2],
                'inputs': json.loads(row[3]) if row[3] else None,
                'parameters': json.loads(row[4]) if row[4] else None,
                'source_reference': row[5], 'status': row[6],
                'outputs': row[7], 'errors': row[8],
                'timestamp': row[8]
            }
        return None