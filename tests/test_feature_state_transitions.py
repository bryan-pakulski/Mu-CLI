"""Tests for feature state transitions — derive_feature_state_status and related pathways.

Validates that features properly transition through:
  awaiting_approval → in_progress → completed
"""
import json
import pytest
from mu.session.session import derive_feature_state_status


class TestDeriveFeatureStateStatus:
    """Unit tests for the canonical derive_feature_state_status function."""

    def test_none_input_returns_running(self):
        assert derive_feature_state_status(None) == "running"

    def test_non_dict_returns_running(self):
        assert derive_feature_state_status("not a dict") == "running"

    def test_not_approved_returns_awaiting_approval(self):
        plan = {"approved": False}
        assert derive_feature_state_status(plan) == "awaiting_approval"

    def test_approved_default_returns_awaiting_approval(self):
        plan = {}
        assert derive_feature_state_status(plan) == "awaiting_approval"

    def test_approved_with_active_tasks_returns_in_progress(self):
        plan = {
            "approved": True,
            "tasks": [
                {"status": "in_progress"},
                {"status": "completed"},
            ],
        }
        assert derive_feature_state_status(plan) == "in_progress"

    def test_approved_with_blocked_task_returns_in_progress(self):
        plan = {
            "approved": True,
            "tasks": [
                {"status": "blocked"},
            ],
        }
        assert derive_feature_state_status(plan) == "in_progress"

    def test_approved_with_non_archived_tasks_returns_in_progress(self):
        plan = {
            "approved": True,
            "tasks": [
                {"status": "completed"},
                {"status": "pending"},
            ],
        }
        assert derive_feature_state_status(plan) == "in_progress"

    def test_approved_all_phases_done_returns_completed(self):
        """When all phases are done and no active tasks, status is completed."""
        plan = {
            "approved": True,
            "phases_completed": True,
            "next_phase": None,
            "tasks": [
                {"status": "completed"},
            ],
        }
        assert derive_feature_state_status(plan) == "completed"

    def test_approved_phases_done_but_in_progress_task_returns_in_progress(self):
        """Even if phases_completed, in_progress tasks take priority."""
        plan = {
            "approved": True,
            "phases_completed": True,
            "next_phase": None,
            "tasks": [
                {"status": "in_progress"},
            ],
        }
        assert derive_feature_state_status(plan) == "in_progress"

    def test_review_status_completed_returns_completed(self):
        plan = {
            "approved": True,
            "review_status": "completed",
        }
        assert derive_feature_state_status(plan) == "completed"

    def test_approved_with_no_tasks_returns_running(self):
        plan = {"approved": True}
        assert derive_feature_state_status(plan) == "running"

    def test_approved_all_archived_tasks_returns_running(self):
        plan = {
            "approved": True,
            "tasks": [
                {"status": "archived"},
                {"status": "archived"},
            ],
        }
        assert derive_feature_state_status(plan) == "running"

    def test_approved_with_none_status_tasks_filters_correctly(self):
        """Tasks with None status should not count as active."""
        plan = {
            "approved": True,
            "tasks": [
                {"status": None},
            ],
        }
        assert derive_feature_state_status(plan) == "running"

    def test_full_lifecycle_awaiting_to_in_progress_to_completed(self):
        """Simulates a full lifecycle: awaiting_approval → in_progress → completed."""
        # Step 1: Created, not approved
        plan = {"approved": False, "tasks": [{"status": "not_started"}]}
        assert derive_feature_state_status(plan) == "awaiting_approval"

        # Step 2: Approved, tasks start
        plan["approved"] = True
        plan["tasks"][0]["status"] = "in_progress"
        assert derive_feature_state_status(plan) == "in_progress"

        # Step 3: All phases done, task completed
        plan["tasks"][0]["status"] = "completed"
        plan["phases_completed"] = True
        plan["next_phase"] = None
        assert derive_feature_state_status(plan) == "completed"