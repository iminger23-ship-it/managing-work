class CoreSession:
    """Core-level state attached to an existing legacy Session."""
    def __init__(self, legacy_session):
        self.legacy = legacy_session
        self.turn_count = 0
        self.last_intent = None
        self.last_tools = []
        self.last_interaction_id = getattr(legacy_session, "last_interaction_id", None)
        self.last_learned_trigger = getattr(legacy_session, "last_learned_trigger", None)

    @property
    def conversation_history(self):
        return self.legacy.conversation_history

    @property
    def pending_action(self):
        return self.legacy.pending_action

    @pending_action.setter
    def pending_action(self, value):
        self.legacy.pending_action = value

    def record(self, intent, tools=None):
        self.turn_count += 1
        self.last_intent = intent
        self.last_tools = list(tools or [])
