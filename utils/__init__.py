"""
Utility modules for research tools.
"""

from .citation_manager import (
    CitationManager,
    Source,
    SourceType,
    get_citation_manager,
    reset_citation_manager,
    register_source,
    get_citation,
    compile_bibliography,
)

__all__ = [
    "CitationManager",
    "Source",
    "SourceType",
    "get_citation_manager",
    "reset_citation_manager",
    "register_source",
    "get_citation",
    "compile_bibliography",
]