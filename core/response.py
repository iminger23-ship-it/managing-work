from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class CoreResponse:
    source: str
    text: str
    intent: str = "UNKNOWN"
    tools_used: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_legacy_tuple(self):
        return self.source, self.text
