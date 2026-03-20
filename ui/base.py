from abc import ABC, abstractmethod


class BaseUI(ABC):
    @abstractmethod
    def render_message(self, role, content, model_name=None):
        """Render a message from a user or assistant."""
        pass

    @abstractmethod
    def get_input(self, session_name, staged_files, agent_mode="default"):
        """Get input from the user."""
        pass

    @abstractmethod
    def show_error(self, message):
        """Display an error message."""
        pass

    @abstractmethod
    def show_info(self, message):
        """Display info message."""
        pass

    @abstractmethod
    def show_status(self, message):
        """Context manager or similar for displaying status (e.g. 'Thinking...')"""
        pass

    @abstractmethod
    def show_tool_result(self, result_str):
        """Display the result of a tool execution with appropriate color-coding."""
        pass
