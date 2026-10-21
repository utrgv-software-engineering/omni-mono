from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillProperties:
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        result: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.license is not None:
            result["license"] = self.license
        if self.compatibility is not None:
            result["compatibility"] = self.compatibility
        if self.allowed_tools is not None:
            result["allowed-tools"] = self.allowed_tools
        if self.metadata:
            result["metadata"] = self.metadata
        return result
