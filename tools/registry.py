from dataclasses import dataclass
from typing import Callable, Dict, Optional

@dataclass
class ToolDefinition:
    name: str
    handler: Callable
    description: str = ""
    requires_approval: bool = False

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, name, handler, description="", requires_approval=False):
        self._tools[name] = ToolDefinition(
            name=name,
            handler=handler,
            description=description,
            requires_approval=requires_approval,
        )

    def get(self, name) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list(self):
        return dict(self._tools)
