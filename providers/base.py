# Abstract LLMProvider and standardized message schemas
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class FileReference:
    uri: str
    mime_type: str
    display_name: str


@dataclass
class MessagePart:
    type: str  # 'text', 'file', 'tool_call', 'tool_result', 'image_inline'
    text: Optional[str] = None
    file_ref: Optional[FileReference] = None
    inline_data: Optional[bytes] = None

    # For agentic tool calls (Model -> User)
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    thought_signature: Optional[str] = None

    # For agentic tool results (User -> Model)
    tool_result: Optional[Any] = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema format
    requires_approval: bool = (
        True  # Tools require approval by default unless downgraded
    )


@dataclass
class Message:
    role: str  # 'user', 'assistant', 'system', 'tool'
    parts: List[MessagePart] = field(default_factory=list)


@dataclass
class ProviderResponse:
    text: str
    parts: List[MessagePart]
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class LLMProvider(ABC):
    def __init__(self, model_name: str = ""):
        self.model_name = model_name

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """Returns a list of available model names for this provider"""
        pass

    @abstractmethod
    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
    ) -> ProviderResponse:
        """Sends the standardized conversation history to the LLM."""
        pass

    @abstractmethod
    def upload_file(self, file_path: str, mime_type: str) -> Optional[FileReference]:
        """Uploads a file to the provider's storage mechanism (if required)."""
        pass
