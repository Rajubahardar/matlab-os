"""MATLAB Agent Skills Registration"""
from typing import List

# Register all 16 MATLAB engineering skills
SKILLS = [
    "MATLAB Programming and Debugging",
    "Simulink Model Development",
    "Power Systems Engineering",
    "Control Systems Engineering",
    "Simscape Electrical",
    "Simulation and Analysis",
    "Optimization and Parameter Tuning",
    "Engineering Verification and Validation",
    "Research Paper Reproduction",
    "MATLAB Project Management and Documentation",
    "Electrical Machines Engineering",
    "Power Electronics Engineering",
    "Renewable Energy and Microgrids",
    "Power Quality and Harmonics",
    "Engineering Data Acquisition and Processing",
    "MATLAB Function Intelligence",
]


def get_skills() -> List[str]:
    """Get the canonical list of registered skills."""
    return SKILLS.copy()


def register_skills(agent):
    """Register all canonical skills with the agent."""
    for skill_name in SKILLS:
        agent.register_skill(skill_name)
    print(f"Registered {len(SKILLS)} skills for MATLAB Agent.")
