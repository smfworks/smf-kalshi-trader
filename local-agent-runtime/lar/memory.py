from abc import ABC, abstractmethod
from typing import Any, Optional
from pathlib import Path
import json
import structlog

logger = structlog.get_logger("lar.memory")


class MemoryProvider(ABC):
    """Abstract base for memory systems."""
    
    @abstractmethod
    async def store(self, key: str, value: Any, metadata: Optional[dict] = None) -> None:
        """Store a value in memory."""
        pass
    
    @abstractmethod
    async def retrieve(self, key: str) -> Optional[Any]:
        """Retrieve a value by key."""
        pass
    
    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search for relevant memories."""
        pass


class InMemoryProvider(MemoryProvider):
    """Simple in-memory provider for development/testing."""
    
    def __init__(self, max_items: int = 1000):
        self._store: dict[str, Any] = {}
        self._metadata: dict[str, dict] = {}
        self.max_items = max_items
    
    async def store(self, key: str, value: Any, metadata: Optional[dict] = None) -> None:
        if len(self._store) >= self.max_items:
            # Remove oldest entry
            oldest = next(iter(self._store))
            del self._store[oldest]
            if oldest in self._metadata:
                del self._metadata[oldest]
        
        self._store[key] = value
        if metadata:
            self._metadata[key] = metadata
        logger.info("memory_stored", key=key, size=len(str(value)))
    
    async def retrieve(self, key: str) -> Optional[Any]:
        value = self._store.get(key)
        logger.info("memory_retrieved", key=key, found=value is not None)
        return value
    
    async def search(self, query: str, limit: int = 5) -> list[dict]:
        # Simple keyword search for in-memory provider
        # Real implementation would use embeddings
        results = []
        query_lower = query.lower()
        
        for key, value in self._store.items():
            value_str = str(value).lower()
            if query_lower in value_str or query_lower in key.lower():
                results.append({
                    "key": key,
                    "value": value,
                    "metadata": self._metadata.get(key, {}),
                })
                if len(results) >= limit:
                    break
        
        logger.info("memory_searched", query=query, results=len(results))
        return results


class FileMemoryProvider(MemoryProvider):
    """File-based persistent memory provider."""
    
    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = {}
    
    def _key_to_path(self, key: str) -> Path:
        """Convert key to safe filesystem path."""
        # Simple hash-based filename
        import hashlib
        safe_name = hashlib.sha256(key.encode()).hexdigest()[:16] + ".json"
        return self.base_path / safe_name
    
    async def store(self, key: str, value: Any, metadata: Optional[dict] = None) -> None:
        path = self._key_to_path(key)
        data = {
            "key": key,
            "value": value,
            "metadata": metadata or {},
            "timestamp": __import__('time').time(),
        }
        
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        
        self._cache[key] = value
        logger.info("memory_persisted", key=key, path=str(path))
    
    async def retrieve(self, key: str) -> Optional[Any]:
        if key in self._cache:
            return self._cache[key]
        
        path = self._key_to_path(key)
        if not path.exists():
            return None
        
        with open(path, "r") as f:
            data = json.load(f)
        
        value = data.get("value")
        self._cache[key] = value
        return value
    
    async def search(self, query: str, limit: int = 5) -> list[dict]:
        # File-based keyword search
        results = []
        query_lower = query.lower()
        
        for path in self.base_path.glob("*.json"):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                
                value_str = str(data.get("value", "")).lower()
                key = data.get("key", "")
                
                if query_lower in value_str or query_lower in key.lower():
                    results.append({
                        "key": key,
                        "value": data.get("value"),
                        "metadata": data.get("metadata", {}),
                    })
                    
                if len(results) >= limit:
                    break
            except Exception:
                continue
        
        return results


class MemoryManager:
    """Manages short-term and long-term memory."""
    
    def __init__(self, short_term: MemoryProvider, long_term: MemoryProvider):
        self.short_term = short_term
        self.long_term = long_term
        self._logger = structlog.get_logger("lar.memory")
    
    async def store_context(self, key: str, value: Any, persist: bool = False) -> None:
        """Store in short-term memory, optionally persist to long-term."""
        await self.short_term.store(key, value)
        
        if persist:
            await self.long_term.store(key, value)
            self._logger.info("context_persisted", key=key)
    
    async def recall(self, key: str) -> Optional[Any]:
        """Try short-term first, then long-term."""
        value = await self.short_term.retrieve(key)
        if value is not None:
            return value
        
        value = await self.long_term.retrieve(key)
        if value is not None:
            # Promote to short-term
            await self.short_term.store(key, value)
        
        return value
    
    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search both memory tiers."""
        short_results = await self.short_term.search(query, limit)
        long_results = await self.long_term.search(query, limit)
        
        # Combine and deduplicate
        seen = set()
        combined = []
        for result in short_results + long_results:
            key = result["key"]
            if key not in seen:
                seen.add(key)
                combined.append(result)
            if len(combined) >= limit:
                break
        
        return combined
