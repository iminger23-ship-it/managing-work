class SafetyManager:
    """Core safety boundary. Existing approval logic remains authoritative."""

    def pending_message(self, description):
        return f"CONFIRM: {description}\napprove / cancel"

    def validate_tool(self, tool):
        return bool(tool)
