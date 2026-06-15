from abc import ABC, abstractmethod
from typing import Any, Optional, AsyncIterator
import json
import httpx
import structlog

logger = structlog.get_logger("lar.llm")


class LLMResponse:
    """Structured LLM response."""
    def __init__(self, content: str, tool_calls: Optional[list[dict]] = None, model: str = ""):
        self.content = content
        self.tool_calls = tool_calls or []
        self.model = model
        self.finish_reason: Optional[str] = None


class LLMBackend(ABC):
    """Abstract base for LLM backends."""
    
    @abstractmethod
    async def chat(self, messages: list[dict], tools: Optional[list[dict]] = None) -> LLMResponse:
        """Send chat request and return response."""
        pass
    
    @abstractmethod
    async def health_check(self) -> dict:
        """Check backend health."""
        pass


class OllamaBackend(LLMBackend):
    """Ollama API backend for local LLMs."""
    
    def __init__(self, model: str, base_url: str = "http://localhost:11434", timeout: float = 120.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
        logger.info("ollama_backend_initialized", model=model, base_url=base_url)
    
    async def chat(self, messages: list[dict], tools: Optional[list[dict]] = None) -> LLMResponse:
        """Send chat request via Ollama API."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
            }
        }
        
        if tools:
            payload["tools"] = tools
        
        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            message = data.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])
            
            # Normalize tool calls to standard format
            normalized_tools = []
            for tc in tool_calls:
                normalized_tools.append({
                    "type": "function",
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", {}),
                    }
                })
            
            logger.info(
                "ollama_chat_complete",
                model=self.model,
                content_length=len(content),
                tool_calls=len(normalized_tools),
            )
            
            return LLMResponse(
                content=content,
                tool_calls=normalized_tools,
                model=self.model,
            )
            
        except httpx.HTTPStatusError as e:
            logger.error("ollama_chat_http_error", status=e.response.status_code, detail=str(e))
            raise
        except httpx.RequestError as e:
            logger.error("ollama_chat_request_error", error=str(e))
            raise
    
    async def health_check(self) -> dict:
        """Check Ollama server health."""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            models = [m["name"] for m in data.get("models", [])]
            
            # Check if our model is available
            model_available = any(self.model in m for m in models)
            
            return {
                "status": "healthy" if model_available else "degraded",
                "available_models": models,
                "target_model": self.model,
                "model_available": model_available,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


class FallbackBackend(LLMBackend):
    """Backend that tries multiple models in sequence."""
    
    def __init__(self, backends: list[LLMBackend], fallback_models: list[str]):
        self.backends = backends
        self.fallback_models = fallback_models
        self._current_backend_index = 0
    
    async def chat(self, messages: list[dict], tools: Optional[list[dict]] = None) -> LLMResponse:
        """Try backends in sequence until one succeeds."""
        for i, backend in enumerate(self.backends):
            try:
                logger.info("trying_backend", index=i, model=backend.model)
                response = await backend.chat(messages, tools)
                if response.content or response.tool_calls:
                    return response
            except Exception as e:
                logger.warning("backend_failed", index=i, model=backend.model, error=str(e))
                continue
        
        raise RuntimeError("All LLM backends failed")
    
    async def health_check(self) -> dict:
        """Check all backends."""
        results = []
        for backend in self.backends:
            try:
                result = await backend.health_check()
                results.append({"model": backend.model, "status": result})
            except Exception as e:
                results.append({"model": backend.model, "status": {"error": str(e)}})
        
        return {
            "backends": results,
            "healthy_count": sum(1 for r in results if r["status"].get("status") == "healthy"),
        }
    
    async def close(self):
        """Close all backends."""
        for backend in self.backends:
            await backend.close()
