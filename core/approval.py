"""Backward-compatible re-export shim.

The body of this module moved to `mu/agent/approval.py` during the
Phase 6 namespace rename. New code should import from ``mu.agent.approval``.
"""

from mu.agent.approval import *  # noqa: F401,F403
from mu.agent.approval import (  # noqa: F401
    ApprovalPlan,
    ModificationPreview,
    build_approval_plan,
    build_approval_prompt,
    collect_approval_plans,
)
