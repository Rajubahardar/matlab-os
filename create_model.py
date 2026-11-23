"""Provider-Independent Model Interface"""
import abc
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ModelProvider(abc.ABC):
    """Abstract base class for model providers."""
    
    @abc.abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate a response from the model."""
        pass
    
    @abc.abstractmethod
    def analyze(self, data: Any, **kwargs) -> Dict[str, Any]:
        """Analyze data using the model."""
        pass
    
    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if the model provider is available."""
        pass


class OllamaProvider(ModelProvider):
    """Ollama model provider integration."""
    
    def __init__(self, host: str = "localhost", model: str = "llama2"):
        self.host = host
        self.model = model
        
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text using Ollama."""
        try:
            import requests
            response = requests.post(
                f"http://{self.host}:11434/api/generate",
                json={"model": self.model, "prompt": prompt, **kwargs},
                timeout=60
            )
            if response.status_code == 200:
                return response.json().get("response", "")
        except Exception as e:
            logger.warning(f"Ollama generation failed: {e}")
        return ""
    
    def analyze(self, data: Any, **kwargs) -> Dict[str, Any]:
        """Analyze data using Ollama."""
        prompt = f"Analyze the following data and provide insights: {data}"
        response = self.generate(prompt, **kwargs)
        return {"analysis": response, "provider": "ollama"}
    
    def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            import requests
            response = requests.get(f"http://{self.host}:11434/api/tags", timeout=2)
            return response.status_code == 200
        except Exception:
            return False


class CloudLLMProvider(ModelProvider):
    """Cloud LLM API provider (OpenAI, Anthropic, etc.)."""
    
    def __init__(self, api_key: str, provider: str = "openai", model: str = "gpt-4"):
        self.api_key = api_key
        self.provider = provider
        self.model = model
        
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text using cloud LLM."""
        logger.info(f"Cloud LLM generation: {self.provider}/{self.model}")
        return ""
    
    def analyze(self, data: Any, **kwargs) -> Dict[str, Any]:
        """Analyze data using cloud LLM."""
        logger.info(f"Cloud LLM analysis: {self.provider}/{self.model}")
        return {"analysis": "", "provider": self.provider}
    
    def is_available(self) -> bool:
        """Check if cloud LLM is available."""
        return self.api_key is not None


class LocalModelProvider(ModelProvider):
    """Local model provider placeholder."""
    
    def __init__(self):
        self.available = False
        
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text locally."""
        return ""
    
    def analyze(self, data: Any, **kwargs) -> Dict[str, Any]:
        """Analyze data locally."""
        return {"analysis": "", "provider": "local"}
    
    def is_available(self) -> bool:
        """Check availability."""
        return self.available