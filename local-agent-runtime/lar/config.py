from typing import Any, Optional
from pathlib import Path
import os
import yaml
from pydantic import BaseModel, Field, validator


class ModelConfig(BaseModel):
    """LLM backend configuration."""
    provider: str = "ollama"
    model: str = "kimi-k2.6"
    base_url: str = "http://localhost:11434"
    api_key: Optional[str] = None
    timeout: float = 120.0
    max_retries: int = 3
    fallbacks: list[str] = Field(default_factory=list)


class ToolConfig(BaseModel):
    """Tool registration configuration."""
    name: str
    enabled: bool = True
    module: str
    config: dict[str, Any] = Field(default_factory=dict)


class MemoryConfig(BaseModel):
    """Memory system configuration."""
    short_term_limit: int = 10
    vault_path: Path = Path("~/GabrielVault")
    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"


class IdentityConfig(BaseModel):
    """Session identity validation configuration."""
    enabled: bool = True
    hmac_secret: Optional[str] = None
    max_payload_age_seconds: int = 300
    strict_session_key: bool = True


class RuntimeConfig(BaseModel):
    """Top-level runtime configuration."""
    agent_id: str
    agent_name: str
    session_key: str
    
    model: ModelConfig = Field(default_factory=ModelConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    tools: list[ToolConfig] = Field(default_factory=list)
    
    log_level: str = "INFO"
    log_format: str = "json"
    
    @validator("log_level")
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v.upper()


class ConfigManager:
    """Manages runtime configuration from YAML files and environment variables."""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or self._find_config()
        self._config: Optional[RuntimeConfig] = None
    
    def _find_config(self) -> Path:
        """Find config file in standard locations."""
        candidates = [
            Path("config/local.yaml"),
            Path("config/default.yaml"),
            Path.home() / ".config" / "lar" / "config.yaml",
            Path("/etc/lar/config.yaml"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("No config file found. Create config/local.yaml")
    
    def load(self) -> RuntimeConfig:
        """Load and validate configuration."""
        with open(self.config_path, "r") as f:
            raw = yaml.safe_load(f)
        
        # Environment variable substitution
        raw = self._substitute_env(raw)
        
        self._config = RuntimeConfig(**raw)
        return self._config
    
    def _substitute_env(self, obj: Any) -> Any:
        """Recursively substitute ${VAR} and ${VAR:default} patterns."""
        if isinstance(obj, dict):
            return {k: self._substitute_env(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_env(item) for item in obj]
        elif isinstance(obj, str):
            return self._expand_env_vars(obj)
        return obj
    
    @staticmethod
    def _expand_env_vars(value: str) -> str:
        """Expand ${VAR} and ${VAR:default} in string values."""
        import re
        
        def replace(match):
            var_expr = match.group(1)
            if ":" in var_expr:
                var_name, default = var_expr.split(":", 1)
                return os.environ.get(var_name, default)
            return os.environ.get(var_expr, match.group(0))
        
        return re.sub(r'\$\{([^}]+)\}', replace, value)
    
    @property
    def config(self) -> RuntimeConfig:
        """Get cached configuration, loading if necessary."""
        if self._config is None:
            self.load()
        return self._config
