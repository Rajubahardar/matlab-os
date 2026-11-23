"""Test MATLAB OS module imports"""
from matlab_os.main import main
from matlab_os.agent.matlab_agent import MATLABAgent
from matlab_os.agent.skills import SKILLS, register_skills
from matlab_os.memory.database import DatabaseService
from matlab_os.intelligence.model_provider import ModelProvider, OllamaProvider
from matlab_os.validation.engineering_validator import EngineeringValidator
from matlab_os.integration.mcp_client import MCPClient, run_matlab_code

print("All imports successful!")
print(f"Skills: {SKILLS}")
print(f"Number of skills: {len(SKILLS)}")

# Test agent initialization
agent = MATLABAgent()
agent.initialize()
print(f"Agent skills: {agent.get_skills()}")

# Test database
db = DatabaseService()
project_id = db.store_project("Test Project", objectives="Test objectives")
print(f"Stored project ID: {project_id}")

project = db.get_project(project_id)
print(f"Retrieved project: {project}")

# Store experiment
exp_id = db.store_experiment(project_id, "test_exp", 
                              inputs={"signal": "sin"}, 
                              parameters={"amplitude": 1.0},
                              source_reference="demo")
print(f"Stored experiment ID: {exp_id}")

# Store evidence
evidence_id = db.store_evidence(exp_id, "RMS value should be 0.707")
print(f"Stored evidence ID: {evidence_id}")

# Store and approve approval
approval_id = db.store_approval("code_execution", requested_by="test_user")
approved = db.approve_operation(approval_id, approved=True, decided_by="operator")
print(f"Approval approved: {approved}")

# Close resources
db.close()
print("\nAll tests passed!")