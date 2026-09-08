from abc import ABC, abstractmethod

class ModelBackend(ABC):
    @abstractmethod
    def generate(self, model_input):
        """Return (text, conversation_history)."""
        raise NotImplementedError

    @abstractmethod
    def status(self):
        raise NotImplementedError
