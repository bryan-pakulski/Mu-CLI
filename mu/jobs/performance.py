"""Canonical retrospective performance read-model for durable jobs.

`analysis.py` performs the raw evidence aggregation. This module attaches
orthogonal management state (archive metadata) and defines semantic metrics
that every control plane must interpret identically.
"""

from __future__ import annotations

from typing import Any, Dict

from .analysis import build_job_analysis as _build_raw_analysis
from .analysis import compare_job_analyses as _compare_raw_analyses
from .management import JobManagementService
from .receipt import JobReceiptBuilder
from .service import JobService


def build_job_performance(
    service: JobService,
    job_id: str,
    *,
    timeline_limit: int = 5000,
) -> Dict[str, Any]:
    analysis = _build_raw_analysis(service, job_id, timeline_limit=timeline_limit)

    # Archival is deliberately orthogonal to execution status and lives in the
    # management table. Never infer it from job metadata or runtime state.
    management = JobManagementService(service).state(job_id)
    job = analysis.get("job") or {}
    job["archived"] = bool(management.get("archived"))
    job["archived_at"] = management.get("archived_at")
    job["archived_reason"] = management.get("archived_reason") or ""

    # Verification rows from analysis.py are chronological (oldest first).
    # "First pass" means the original deterministic verification succeeded,
    # even if later reviewer feedback caused additional valid verification runs.
    verifications = analysis.get("verifications") or []
    summary = analysis.get("summary") or {}
    summary["first_pass_verification"] = (
        bool(verifications[0].get("passed")) if verifications else None
    )

    # Cost must preserve the distinction between a true local $0 provider bill
    # and a model/provider for which MuCLI does not have a complete tariff.
    # The receipt owns this provenance because it is persisted per attempt.
    receipt = JobReceiptBuilder(service).build(job_id)
    model_api = ((receipt.get("usage") or {}).get("model_api") or {})
    summary["cost_status"] = str(model_api.get("status") or "legacy")
    summary["cost_complete"] = bool(model_api.get("cost_complete"))
    summary["model_api_cost_usd"] = float(model_api.get("api_cost_usd") or 0.0)
    summary["unpriced_attempts"] = int(model_api.get("unpriced_attempts") or 0)
    summary["billing_modes"] = list(model_api.get("billing_modes") or [])
    analysis["cost"] = model_api
    return analysis


def compare_job_performance(
    service: JobService,
    primary_job_id: str,
    comparison_job_id: str,
    *,
    timeline_limit: int = 1000,
) -> Dict[str, Any]:
    primary = build_job_performance(
        service,
        primary_job_id,
        timeline_limit=timeline_limit,
    )
    reference = build_job_performance(
        service,
        comparison_job_id,
        timeline_limit=timeline_limit,
    )
    return {
        "comparison": _compare_raw_analyses(primary, reference),
        "primary": primary,
        "reference": reference,
    }
