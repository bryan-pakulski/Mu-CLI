from dataclasses import dataclass

from mu.agent.approval import build_approval_plan
from mu.session.session import Session, SessionManager
from mu.tools._dispatcher import execute_tool
from mu.tools.descriptors import build_tool_context, serialize_tool_descriptor
from mu.workspace.folder_context import FolderContext
from providers.base import MessagePart, ProviderResponse


@dataclass
class DummyProvider:
    name: str = "dummy"
    model_name: str = "dummy-model"

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="ok",
            parts=[MessagePart(type="text", text="ok")],
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    def upload_file(self, file_path, mime_type):
        return None


def test_registry_layer_serializes_descriptor_metadata():
    payload = serialize_tool_descriptor("read_file")

    assert payload["name"] == "read_file"
    assert payload["execution_kind"] == "read"
    assert payload["preview_policy"] == "none"
    assert payload["result_mode"] == "structured+collated"
    assert payload["handler_key"] == "read_file"


def test_refined_execution_interface_tracks_invocation_source():
    context = build_tool_context(None, invocation_source="server")

    assert context.invocation_source == "server"


def test_validation_layer_rejects_empty_path_arguments():
    ctx = FolderContext()

    result = execute_tool("read_file", {"filename": ""}, ctx)

    assert "argument is empty" in result


def test_approval_layer_strict_mode_requires_plan_even_for_read_only_tools(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("hello\n", encoding="utf-8")

    plan = build_approval_plan(
        "read_file",
        {"filename": str(target)},
        ctx,
        strict_mode=True,
    )

    assert plan.requires_approval is True
    assert plan.can_approve is True
    assert plan.modifications == []


def test_execution_layer_reports_mixed_batch_failures(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    result = execute_tool(
        "batch_job",
        {
            "commands": [
                {"tool_name": "missing_tool", "tool_args": {}},
                {"tool_name": "batch_job", "tool_args": {"commands": []}},
            ]
        },
        ctx,
    )

    assert "unknown tool: missing_tool" in result
    assert "nested batch_job not allowed" in result


def test_structured_result_layer_preserves_envelope_shape():
    session = Session(DummyProvider(), False, "system instruction", SessionManager())

    structured = session._build_structured_tool_result(
        "write_file",
        {"filename": "note.txt"},
        "Successfully wrote to note.txt",
    )

    assert structured["modified_files"] == ["note.txt"]
    assert structured["artifacts"] == []
    assert structured["telemetry"]["execution_source"] == "session"
    assert structured["error"] is None
