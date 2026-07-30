
"""Durable, session-scoped user attachment storage."""

from .registry import AttachmentError, AttachmentRegistry

__all__ = ["AttachmentError", "AttachmentRegistry"]
