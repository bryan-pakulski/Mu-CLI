import pytest
from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse, MessagePart
from mu.tools.descriptors import TOOLS


class MockUI:
    def __init__(self):
        self.infos = []
        self.diffs = []
        self.errors = []
        self.messages = []
        self.tool_results = []

    def show_info(self, msg):
        self.infos.append(msg)

    def show_diff(self, filename, orig, mod):
        self.diffs.append((filename, orig, mod))

    def show_error(self, msg):
        self.errors.append(msg)

    def render_message(self, role, content, model_name=None):
        self.messages.append((role, content))

    def show_tool_result(self, res):
        self.tool_results.append(res)

    def show_status(self, msg):
        from contextlib import contextmanager

        @contextmanager
        def status_mgr():
            yield

        return status_mgr()


class MockProvider(LLMProvider):
    def __init__(self, responses):
        self.responses = responses
        self.model_name = "mock-model"
        self.calls = 0

    def get_available_models(self):
        return ["mock-model"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        resp = self.responses[self.calls]
        self.calls += 1
        return resp

    def upload_file(self, file_path, mime_type):
        return None


def test_multiple_diffs_handling(tmp_path):
    # Setup files
    file1 = tmp_path / "file1.txt"
    file1.write_text("line1\nline2")
    file2 = tmp_path / "file2.txt"
    file2.write_text("alpha\nbeta")

    sm = SessionManager()
    ui = MockUI()

    # Mock multiple apply_diff calls
    p1 = MessagePart(
        type="tool_call",
        tool_name="apply_diff",
        tool_args={
            "filename": str(file1),
            "diff": f"--- {file1}\n+++ {file1}\n@@ -1,2 +1,2 @@\n-line1\n+line1_new\n line2",
        },
    )
    p2 = MessagePart(
        type="tool_call",
        tool_name="apply_diff",
        tool_args={
            "filename": str(file2),
            "diff": f"--- {file2}\n+++ {file2}\n@@ -1,2 +1,2 @@\n-alpha\n+alpha_new\n beta",
        },
    )

    # Response with 2 tool calls, then final text
    resp1 = ProviderResponse(
        text="", parts=[p1, p2], input_tokens=10, output_tokens=10, total_tokens=20
    )
    resp2 = ProviderResponse(
        text="Done!",
        parts=[MessagePart(type="text", text="Done!")],
        input_tokens=5,
        output_tokens=5,
        total_tokens=10,
    )

    provider = MockProvider([resp1, resp2])
    session = Session(provider, False, "system", sm, ui=ui)
    session.variables["strict_mode"] = False  # To avoid Prompt.ask on every tool call

    session.send_message("test")

    # Verify both tool results are present
    assert len(ui.tool_results) == 2
    assert "Successfully applied diff to" in ui.tool_results[0]
    assert "Successfully applied diff to" in ui.tool_results[1]

    # Verify files were updated
    assert file1.read_text() == "line1_new\nline2"
    assert file2.read_text() == "alpha_new\nbeta"
