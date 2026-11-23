"""SQLite Database Service for MATLAB OS"""
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
        
        self.conn.commit()