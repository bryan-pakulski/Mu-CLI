"""User-selected OpenAI Chat Completions servers, independent of cloud credentials."""
import os
from urllib.parse import urlsplit, urlunsplit

from openai import OpenAI

from .base import LLMProvider
from .openai import OpenAIProvider

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"


def normalize_base_url(value):
    value = str(value or DEFAULT_BASE_URL).strip().rstrip("/")
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError("Local endpoint must be an HTTP(S) base URL without credentials, query or fragment.")
    try:
        parts.port
    except ValueError:
        raise ValueError("Invalid local endpoint port.") from None
    path = parts.path.rstrip("/") or "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class LocalOpenAIProvider(OpenAIProvider):
    def __init__(self, model_name="", base_url=None, api_key=None, request_timeout=900):
        # Deliberately bypass OpenAIProvider's cloud defaults and credentials.
        LLMProvider.__init__(self, model_name)
        self.name = "local"
        self._initial_url = base_url
        self._initial_key = api_key
        self._session_variables = {}
        self._metadata = {}
        self._discovered = False
        self.discovery_error = ""
        self._connection = None
        self._initial_timeout = request_timeout
        self.apply_session_variables({})

    def apply_session_variables(self, variables):
        url = normalize_base_url(variables.get("local_base_url", self._initial_url)
                                 or os.environ.get("LOCAL_LLM_BASE_URL") or DEFAULT_BASE_URL)
        key = variables.get("local_api_key", self._initial_key) or os.environ.get("LOCAL_LLM_API_KEY") or "local"
        timeout = float(variables.get("local_request_timeout") or self._initial_timeout)
        if not 1 <= timeout <= 86400:
            raise ValueError("Local request timeout must be 1..86400 seconds.")
        self._session_variables = variables
        connection = (url, key, timeout)
        if connection != self._connection:
            client = OpenAI(base_url=url, api_key=key, organization="", project="", max_retries=0, timeout=timeout)
            old = getattr(self, "_client", None)
            self._client = client
            self.BASE_URL = url
            self._connection = connection
            self._metadata = {}
            self._discovered = False
            if old is not None:
                old.close()

    def get_available_models(self):
        """Refresh the advertised model list; never invent cloud fallback models."""
        try:
            rows = self._client.models.list(timeout=5).data
            self._metadata = {row.id: row.model_dump() for row in rows if isinstance(row.id, str) and row.id}
            self.discovery_error = "" if self._metadata else "This server advertises no models. Load a model and refresh."
        except Exception as exc:
            self._metadata = {}
            # Server errors may echo headers, secrets or prompts. Report only type/status.
            status = getattr(exc, "status_code", None)
            self.discovery_error = f"Local model discovery failed ({type(exc).__name__}{' '+str(status) if status else ''}). Check the endpoint and start the server."
        self._discovered = True
        return sorted(self._metadata, key=str.lower)

    def _model_info(self, model_name=None):
        if not self._discovered:
            self.get_available_models()
        return self._metadata.get(model_name or self.model_name, {})

    def _use_responses(self, model, messages, variables):
        return False

    def clone_for_child(self):
        # Child endpoint changes must not close the parent's live HTTP client.
        child = LocalOpenAIProvider(self.model_name, self.BASE_URL, self._client.api_key, self._connection[2])
        child.apply_session_variables(dict(self._session_variables))
        child._metadata = dict(self._metadata)
        child._discovered = self._discovered
        return child

    def effective_context_window(self, model_name=None):
        info = self._model_info(model_name)
        advertised = info.get("context_window") or info.get("max_model_len")
        override = self._session_variables.get("local_context_window")
        values = [v for v in (advertised, override) if isinstance(v, int) and not isinstance(v, bool) and v > 0]
        return min(values) if values else 4096

    def effective_response_reserve(self, model_name=None):
        value = self._session_variables.get("local_max_tokens") or 2048
        cap = self._model_info(model_name).get("max_output_tokens")
        return min(max(1, int(value)), cap if isinstance(cap, int) and cap > 0 else 2048,
                   max(1, self.effective_context_window(model_name) // 2))

    def compaction_safety_factor(self):
        return 1.3

    def supported_input_modalities(self):
        # The initial adapter promises text + function tools only.
        return ("text",)

    def _prepare_chat_payload(self, payload, *, thinking, reasoning_effort):
        if not self.model_name:
            raise ValueError("Select a local model before sending a message.")
        for key in ("prompt_cache_key", "prompt_cache_retention", "reasoning_effort"):
            payload.pop(key, None)
        payload["max_tokens"] = self.effective_response_reserve()
        supported = self._model_info().get("capabilities") or {}
        if isinstance(supported, dict) and supported.get("reasoning_effort"):
            effort = reasoning_effort or ("high" if thinking else "low")
            payload["reasoning_effort"] = effort if effort in ("low", "medium", "high") else "low"
        return payload
