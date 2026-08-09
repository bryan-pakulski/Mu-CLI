"""Compact evidence receipt for understanding a durable job at a glance."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

from utils.config import HISTORY_DIR

from .service import JobService
from .verification import VerificationStore


RECEIPT_SCHEMA_VERSION = 1


class JobReceiptBuilder:
    def __init__(self, service: JobService, *, root: str | None = None):
        self.service = service
        self.root = os.path.abspath(
            os.path.expanduser(root or os.path.join(HISTORY_DIR, "jobs", "evidence"))
        )
        os.makedirs(self.root, exist_ok=True)
        self.verifications = VerificationStore(service.store, evidence_root=self.root)

    @staticmethod
    def _token_totals(attempts) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for attempt in attempts:
            result = attempt.metadata.get("agent_result") if isinstance(attempt.metadata, dict) else None
            tokens = result.get("tokens") if isinstance(result, dict) else None
            if not isinstance(tokens, dict):
                continue
            for key, value in tokens.items():
                if isinstance(value, (int, float)):
                    totals[str(key)] = totals.get(str(key), 0.0) + float(value)
        return totals

    def build(self, job_id: str) -> Dict[str, Any]:
        job = self.service.get(job_id)
        attempts = self.service.attempts(job_id)
        events = self.service.events(job_id)
        verification = self.verifications.latest(job_id)
        elapsed = 0.0
        if job.started_at is not None:
            end = job.completed_at or job.updated_at or time.time()
            elapsed = max(0.0, float(end) - float(job.started_at))

        event_counts: Dict[str, int] = {}
        for event in events:
            event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1

        receipt: Dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "generated_at": time.time(),
            "job": {
                "id": job.id,
                "title": job.title,
                "description": job.description,
                "status": job.status.value,
                "needs_attention": job.needs_attention,
                "attention_reason": job.attention_reason.value,
                "attention_detail": job.attention_detail,
            },
            "outcome": {
                "ready_for_review": job.status.value == "ready_for_review",
                "terminal": job.terminal,
                "attempts": len(attempts),
                "elapsed_seconds": elapsed,
                "cost_usd": float(job.cost_usd or 0.0),
            },
            "ticket": {
                "acceptance_criteria": list(job.acceptance_criteria),
                "validation_commands": list(job.validation_commands),
            },
            "git": {
                "repository": job.repository,
                "repository_id": job.metadata.get("repository_id") if isinstance(job.metadata, dict) else None,
                "base_branch": job.base_branch,
                "base_sha": job.base_sha,
                "branch": job.branch,
                "worktree": job.worktree,
                "head_sha": verification.head_sha if verification else "",
                "changed_files": verification.changed_files if verification else [],
                "additions": verification.additions if verification else 0,
                "deletions": verification.deletions if verification else 0,
                "diff_stat": verification.diff_stat if verification else "",
                "dirty": verification.dirty if verification else None,
            },
            "verification": verification.to_dict() if verification else None,
            "attempts": [attempt.to_dict() for attempt in attempts],
            "usage": {
                "cost_usd": float(job.cost_usd or 0.0),
                "tokens": self._token_totals(attempts),
            },
            "activity": {
                "events": len(events),
                "agent_messages": event_counts.get("agent_message", 0),
                "tool_calls": event_counts.get("tool_call_ui", 0),
                "human_responses": event_counts.get("human_response", 0),
                "checkpoints": event_counts.get("checkpoint_created", 0),
                "verification_runs": event_counts.get("verification_evidence_created", 0),
            },
        }
        return receipt

    def write(self, job_id: str) -> str:
        job_dir = os.path.join(self.root, job_id)
        os.makedirs(job_dir, exist_ok=True)
        path = os.path.join(job_dir, "work-receipt.json")
        receipt = self.build(job_id)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, indent=2, default=str)
        self.service.store.append_event(
            job_id,
            "work_receipt_updated",
            payload={"path": path, "schema_version": RECEIPT_SCHEMA_VERSION},
        )
        return path
