from .base import ModelBackend

class LegacyOllamaBackend(ModelBackend):
    """
    Compatibility backend for the existing OpenAI Agents + Ollama stack.
    Core does not know implementation details.
    """
    def __init__(self, runner, assistant, lock):
        self.runner = runner
        self.assistant = assistant
        self.lock = lock

    def generate(self, model_input):
        with self.lock:
            result = self.runner.run_sync(self.assistant, model_input)
        return result.final_output, result.to_input_list()

    def status(self):
        return {"backend": "ollama", "ready": True}
