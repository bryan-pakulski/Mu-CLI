"""
Utility modules for research tools.
"""

from .citation_manager import (
    CitationManager,
    Source,
    SourceType,
    SOURCE_TYPE_CAPS,
    get_citation_manager,
    reset_citation_manager,
    register_source,
    set_research_topic,
    get_current_research_topic,
    get_citation,
    compile_bibliography,
)
from .threads import NamedThread, set_os_thread_name

__all__ = [
    "CitationManager",
    "NamedThread",
    "Source",
    "SourceType",
    "SOURCE_TYPE_CAPS",
    "get_citation_manager",
    "reset_citation_manager",
    "register_source",
    "set_research_topic",
    "get_current_research_topic",
    "get_citation",
    "compile_bibliography",
    "set_os_thread_name",
]
