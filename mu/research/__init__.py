"""Research mode engine: sources, evidence ledger, extraction and reports.

``sources`` holds the per-session ``ResearchState``; ``ledger`` the question /
claim / evidence records and confidence rules; ``urls`` canonicalisation and
origin grouping; ``extract`` main-content extraction and passage ranking;
``report`` the bibliography, report skeleton and exports; ``texts`` the store
of extracted source text used for excerpt checks.
"""

from .sources import (
    DEFAULT_TOPIC,
    SOURCE_TYPE_CAPS,
    CitationManager,
    ResearchError,
    ResearchState,
    Source,
    SourceType,
    current_research_state,
    get_citation_manager,
    reset_citation_manager,
    source_type_cap,
    use_research_state,
)

__all__ = [
    "DEFAULT_TOPIC",
    "SOURCE_TYPE_CAPS",
    "CitationManager",
    "ResearchError",
    "ResearchState",
    "Source",
    "SourceType",
    "current_research_state",
    "get_citation_manager",
    "reset_citation_manager",
    "source_type_cap",
    "use_research_state",
]
