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
from .threads import NamedThread, set_os_thread_name

__all__ = [
    "CitationManager",
    "NamedThread",
    "Source",
    "SourceType",
    "get_citation_manager",
    "reset_citation_manager",
    "register_source",
    "get_citation",
    "compile_bibliography",
    "set_os_thread_name",
]
