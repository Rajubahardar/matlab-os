"""MATLAB OS Main Entry Point"""
import sys
from matlab_os.agent.matlab_agent import MATLABAgent

def main():
    """Main entry point for the MATLAB Expert Agent."""
    print("MATLAB Expert Agent starting...")
    agent = MATLABAgent()
    print("Agent initialized successfully.")

if __name__ == "__main__":
    main()