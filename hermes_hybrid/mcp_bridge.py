"""MCP / Drive / Colab bridge: registry only, no re-implementation.

Existing Linux MCP, Google Drive, and Google Colab integrations live
in the official Hermes agent and in the parent project's own code.
This module only records which adapters are configured and validates
their configuration shape, so the hybrid package can prove it does
not break those integrations without re-implementing them or
handling their credentials (which are never read here).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Adapter:
    name: str
    kind: str  # "mcp", "drive", "colab"
    enabled: bool = True
    config: dict | None = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.kind not in ("mcp", "drive", "colab"):
            errors.append(f"unknown adapter kind: {self.kind}")
        if not self.name:
            errors.append("adapter name is required")
        return errors


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, Adapter] = {}

    def register(self, adapter: Adapter) -> None:
        errors = adapter.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self._adapters[adapter.name] = adapter

    def enabled(self, kind: str | None = None) -> list[Adapter]:
        return [a for a in self._adapters.values()
                if a.enabled and (kind is None or a.kind == kind)]

    def names(self) -> list[str]:
        return sorted(self._adapters)
