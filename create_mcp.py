"""MATLAB MCP Client"""
import logging
import requests
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for MATLAB MCP server communication."""
    
    def __init__(self, host: str = "localhost", port: int = 31515):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.connected = False
        
    def connect(self) -> bool:
        """Connect to the MATLAB MCP server."""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            if response.status_code == 200:
                self.connected = True
                logger.info(f"Connected to MATLAB MCP server at {self.base_url}")
                return True
        except Exception as e:
            logger.warning(f"Cannot connect to MATLAB MCP server: {e}")
        self.connected = False
        return False
    
    def is_connected(self) -> bool:
        """Check if connected to MCP server."""
        return self.connected
    
    def execute_matlab_code(self, code: str, timeout: int = 60) -> Dict[str, Any]:
        """Execute MATLAB code through the MCP server."""
        if not self.connected:
            raise ConnectionError("Not connected to MCP server")
        try:
            response = requests.post(
                f"{self.base_url}/execute",
                json={"code": code, "timeout": timeout},
                timeout=timeout + 10
            )
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"MCP server error: {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}


# Convenience function for quick MATLAB code execution
def run_matlab_code(code: str, host: str = "localhost", port: int = 31515, timeout: int = 60) -> Dict[str, Any]:
    """Run MATLAB code and return results."""
    client = MCPClient(host, port)
    if client.connect():
        return client.execute_matlab_code(code, timeout)
    return {"error": "Could not connect to MATLAB MCP server"}