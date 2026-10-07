from collections import defaultdict
from typing import Any


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, Any]] = defaultdict(dict)

    def register(self, kind: str, name: str, plugin: Any) -> None:
        self._plugins[kind][name] = plugin

    def get(self, kind: str, name: str) -> Any:
        return self._plugins[kind][name]

    def list(self, kind: str) -> list[str]:
        return sorted(self._plugins[kind].keys())


registry = PluginRegistry()
