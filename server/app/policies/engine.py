from dataclasses import dataclass

from server.app.tools.registry import ToolDefinition

@dataclass
class PolicyDecision:
    decision: str
    reason: str


class PolicyEngine:
    def evaluate(self, session_mode: str, tool: ToolDefinition) -> PolicyDecision:
        mode = (session_mode or "interactive").lower()

        if mode == "yolo":
            return PolicyDecision(decision="allow", reason="yolo mode allows all tooling")

        if tool.requires_approval:
            return PolicyDecision(
                decision="escalate",
                reason="Tool requires approval",
            )
        
        return PolicyDecision(decision="allow", reason="tool is low risk")


policy_engine = PolicyEngine()
