"""MATLAB OS Main Entry Point"""
from matlab_os.agent.matlab_agent import MATLABAgent


def main():
    """Start the agent, report its real state, and shut down cleanly."""
    print("MATLAB Expert Agent starting...")
    agent = MATLABAgent()
    try:
        agent.initialize()
        print(f"Agent ready with {len(agent.get_skills())} skills.")
    finally:
        agent.close()


if __name__ == "__main__":
    main()