from .router import IntentRouter
from .context import ContextBuilder
from .safety import SafetyManager
from .response import CoreResponse
from .session import CoreSession
from .learning import LearningStore

class CoreOrchestrator:
    """
    MyLocalAI Core v1.
    Dependency-injected adapter keeps Core independent from the legacy engine.
    """

    def __init__(self, adapter, model_backend, tool_registry=None):
        self.adapter = adapter
        self.model = model_backend
        self.router = IntentRouter()
        self.context = ContextBuilder()
        self.safety = SafetyManager()
        self.tools = tool_registry
        self.learning = self.adapter.learning_store()

    def handle(self, user_input, legacy_session):
        session = CoreSession(legacy_session)
        text = (user_input or "").strip()
        interaction_id = self.learning.begin_interaction(text)
        session.last_interaction_id = interaction_id

        def finish(response):
            ok = response.source not in {"error"}
            self.learning.finish_interaction(interaction_id, response.source, response.intent, response.text, ok)
            # Immediate deterministic commands are safe automatic-learning signals.
            # Model responses never become executable lessons by themselves.
            if ok and response.source == "pc" and response.intent == "PC_COMMAND":
                try:
                    signature = self.adapter.learning_signature(text, response.intent)
                    if signature:
                        self.learning.observe(text, signature, True)
                except Exception:
                    pass
            legacy_session.last_interaction_id = interaction_id
            return response

        intent = self.router.classify(text, self.adapter)
        if intent.name not in {"PENDING_ACTION", "LEARNED_COMMAND"}:
            legacy_session.last_learned_trigger = None

        if intent.name == "EMPTY":
            return finish(CoreResponse("system", "", intent.name))

        if intent.name == "EXIT":
            return finish(CoreResponse("exit", "Goodbye!", intent.name))

        if intent.name == "PENDING_ACTION":
            source, result = self.adapter.resolve_pending_action(text)
            session.record(intent.name)
            return finish(CoreResponse(source, result, intent.name))

        if intent.name == "RESET":
            legacy_session.conversation_history = []
            session.record(intent.name)
            return finish(CoreResponse("system", "AI conversation memory cleared. Hardware/performance commands are unaffected.", intent.name))

        if intent.name == "LEARNING_OPERATION":
            result = self.adapter.learning_command(text)
            session.record(intent.name, ["learning"])
            return finish(CoreResponse("system", result, intent.name, ["learning"]))

        if intent.name == "EXTERNAL_KNOWLEDGE":
            source, result = self.adapter.external_command(text)
            session.record(intent.name, ["internet", "knowledge", "screen_learning"])
            return finish(CoreResponse(source, result, intent.name, ["internet", "knowledge", "screen_learning"]))

        # Exact learned aliases are evaluated before normal action routing. A
        # learned mapping never bypasses safety: learned commands are re-routed
        # through the same deterministic command/action checks as typed input.
        learned = self.adapter.match_learned_rule(text)
        if learned:
            legacy_session.last_learned_trigger = learned.get("trigger")
            routed = self.adapter.execute_learned_rule(learned["command"])
            session.record("LEARNED_COMMAND", ["learning", "safety_router"])
            return finish(CoreResponse(routed[0], routed[1], "LEARNED_COMMAND", ["learning", "safety_router"]))

        if intent.name == "MEMORY_OPERATION":
            result = self.adapter.memory_command(text)
            session.record(intent.name, ["memory"])
            return finish(CoreResponse("system", result, intent.name, ["memory"]))

        if intent.name == "FILE_OPERATION":
            source, result = self.adapter.execute_file_command(text)
            session.record(intent.name, ["file_tools"])
            return finish(CoreResponse(source, result, intent.name, ["file_tools"]))

        if intent.name == "SECURITY_SCAN":
            source, result = self.adapter.execute_scan_command(text)
            session.record(intent.name, ["security_scan"])
            return finish(CoreResponse(source, result, intent.name, ["security_scan"]))

        if intent.name == "SYSTEM_ACTION":
            description = self.adapter.create_pending_action(text)
            session.record(intent.name, ["action_router"])
            return finish(CoreResponse("action_pending", self.safety.pending_message(description), intent.name, ["action_router"]))

        if intent.name == "PC_COMMAND":
            route = self.adapter.route_command(text)
            result = self.adapter.execute_command(route)
            session.record(intent.name, [route])
            return finish(CoreResponse("pc", result, intent.name, [route]))

        # Model / planning path
        agent_input = self.context.build(text, self.adapter)
        learned_context = self.learning.build_context(text)
        if learned_context:
            agent_input = self.adapter.append_learning_context(agent_input, learned_context)
        knowledge_context = self.adapter.knowledge_context(text)
        if knowledge_context:
            agent_input = self.adapter.append_learning_context(agent_input, knowledge_context)
        web_context = getattr(self.adapter, 'web_context', lambda _t: '')(text)
        if web_context:
            agent_input = self.adapter.append_learning_context(agent_input, web_context)
        screen_context = getattr(self.adapter, 'screen_context', lambda: '')()
        if screen_context:
            agent_input = self.adapter.append_learning_context(agent_input, screen_context)
        output, history = self.model.generate(agent_input)
        legacy_session.conversation_history = history
        self.adapter.trim_history(legacy_session)
        session.record(intent.name, ["model_backend"])
        return finish(CoreResponse("ai", output, intent.name, ["model_backend"]))

