class ContextBuilder:
    """Builds model input through an adapter so model backends stay independent."""
    def build(self, user_input, adapter):
        return adapter.build_agent_input(user_input)
