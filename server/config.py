"""Configuration is read per app instance; .env files are not auto-loaded."""
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .persona import ROOT


@dataclass(frozen=True)
class Settings:
    persona_path: Path
    ollama_model: str = "qwen3.5:2b-q4_K_M"
    ollama_url: str = "http://127.0.0.1:11434"
    public_origin: str | None = None
    cookie_secure: bool = False
    inference_timeout: float = 45
    guard_timeout: float = 25
    pipeline_timeout: float = 125

    @classmethod
    def from_env(cls):
        explicit = os.getenv("PERSONA_PATH")
        local = ROOT / "knowledge/persona.local.json"
        path = Path(explicit).expanduser() if explicit else (local if local.exists() else ROOT / "knowledge/persona.example.json")
        if not path.is_absolute():
            path = ROOT / path
        model = os.getenv("OLLAMA_MODEL", cls.ollama_model).strip()
        url = os.getenv("OLLAMA_URL", cls.ollama_url).rstrip("/")
        origin = os.getenv("PUBLIC_ORIGIN") or None
        if not url:
            raise ValueError("OLLAMA_URL must not be empty")
        for value in (url, origin):
            if value is not None:
                parsed = urlsplit(value)
                _ = parsed.port
                if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                        or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or parsed.path not in {"", "/"} or "\\" in value
                        or any(c.isspace() or ord(c) < 32 for c in value)):
                    raise ValueError("Server URLs must be HTTP(S) origins without credentials or paths")
        secure = os.getenv("COOKIE_SECURE", "0")
        if not model or secure not in {"0", "1"}:
            raise ValueError("Invalid OLLAMA_MODEL or COOKIE_SECURE")
        return cls(path, model, url, origin.rstrip("/") if origin else None, secure == "1")
