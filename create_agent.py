"""MATLAB Expert Agent Core"""
import logging
from .skills import register_skills

logger = logging.getLogger(__name__)


class MATLABAgent:
    """Main MATLAB Expert Agent class."""
    
    def __init__(self):
        self.name = "MATLAB Expert Agent"
        self.version = "0.1.0"
        self.skills = []
        self.db = None
        self.initialized = False
        
    def initialize(self):
        """Initialize the agent and all capabilities."""
        logger.info("Initializing MATLAB Expert Agent...")
        register_skills(self)
        self._init_memory()
        self.initialized = True
        logger.info("MATLAB Expert Agent initialized successfully.")
    
    def _init_memory(self):
        """Initialize SQLite memory database."""
        import sqlite3
        from pathlib import Path
        db_path = Path("data/matlab_agent.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self._create_tables()
        
    def _create_tables(self):
        """Create database tables for project memory, experiments, evidence, approvals."""
        if self.db is None:
            return
        cursor = self.db.cursor()
        
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
        
        self.db.commit()
    
    def register_skill(self, skill_name: str):
        """Register a skill with the agent."""
        if skill_name not in self.skills:
            self.skills.append(skill_name)
            logger.info(f"Skill registered: {skill_name}")
    
    def get_skills(self):
        """Get list of registered skills."""
        return self.skills.copy()
    
    def execute_skill(self, skill_name: str, *args, **kwargs):
        """Execute a registered skill."""
        if skill_name not in self.skills:
            raise ValueError(f"Skill not registered: {skill_name}")
        logger.info(f"Executing skill: {skill_name}")
        # Placeholder - skills will be implemented in respective modules
        return f"Skill {skill_name} execution placeholder"
    
    def close(self):
        """Close the agent and release resources."""
        if self.db:
            self.db.close()
        self.initialized = False
        logger.info("MATLAB Expert Agent closed.")