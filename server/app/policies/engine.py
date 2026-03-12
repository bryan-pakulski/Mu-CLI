from dataclasses import dataclass

from server.app.tools.registry import ToolDefinition


@dataclass
class PolicyDecision:
    decision: str
    reason: str


class PolicyEngine:
    def evaluate(self, session_mode: str, tool: ToolDefinition) -> PolicyDecision:
        mode = (session_mode or "interactive").lower()

        if mode == "yolo" and tool.risk_level in {"low", "medium"}:
            return PolicyDecision(decision="allow", reason="yolo mode allows low/medium risk tools")

        if tool.risk_level == "high":
            return PolicyDecision(decision="ask", reason="high risk tool requires approval")

        if tool.requires_approval:
            return PolicyDecision(decision="ask", reason="tool is marked approval-required")

        return PolicyDecision(decision="allow", reason="tool is low risk")


policy_engine = PolicyEngine()
