"""MATLAB Agent Skills Registration"""
from .matlab_agent import MATLABAgent

# Register the 10 skills of the MATLAB Expert Agent
SKILLS = [
    "MATLAB Programming and Debugging",
    "Simulink Model Development",
    "Power Systems Engineering",
    "Simscape Electrical",
    "Control Systems Engineering",
    "Simulation and Analysis",
    "Optimization and Parameter Tuning",
    "Engineering Verification and Validation",
    "Research Paper Reproduction",
    "MATLAB Project Management and Documentation",
]

def register_skills(agent: MATLABAgent):
    """Register all 10 skills with the agent."""
    for skill_name in SKILLS:
        agent.register_skill(skill_name)
    print(f"Registered {len(SKILLS)} skills for MATLAB Agent.")