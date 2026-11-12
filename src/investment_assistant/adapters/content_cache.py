"""Small atomic local cache for bounded public research evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ContentCache:
    def __init__(self, root: Path) -> None:
        self._root = root

    def key_path(self, namespace: str, key: str, suffix: str = ".json") -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._root / namespace / f"{digest}{suffix}"

    def read_json(self, namespace: str, key: str) -> dict[str, Any] | None:
        path = self.key_path(namespace, key)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def write_json(self, namespace: str, key: str, value: dict[str, Any]) -> Path:
        path = self.key_path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        return path

    def read_bytes(self, namespace: str, key: str, suffix: str) -> bytes | None:
        path = self.key_path(namespace, key, suffix)
        try:
            return path.read_bytes() if path.exists() else None
        except OSError:
            return None

    def write_bytes(self, namespace: str, key: str, suffix: str, value: bytes) -> Path:
        path = self.key_path(namespace, key, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{suffix}.tmp")
        temporary.write_bytes(value)
        temporary.replace(path)
        return path
