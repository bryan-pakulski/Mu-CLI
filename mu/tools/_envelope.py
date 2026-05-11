"""Re-export of envelope helpers from the legacy `core/tools.py`.

The envelope shape `{ok, error_code, message, data, artifacts, telemetry}`
is the contract that every tool result must satisfy. Tests in
`tests/test_harness_layers.py` pin it. New tool handlers can return any
of:
  * a plain string                       - wrapped automatically
  * a dict with an `ok` key              - completed via `_ensure_envelope_shape`
  * a fully-formed envelope dict         - passed through unchanged
"""

from core.tools import (
    _build_tool_envelope,
    _envelope_from_handler_result,
    infer_tool_error_code,
)

__all__ = [
    "_build_tool_envelope",
    "_envelope_from_handler_result",
    "infer_tool_error_code",
]
