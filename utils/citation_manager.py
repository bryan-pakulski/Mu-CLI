"""
Citation Manager for tracking sources and generating markdown footnotes.

Provides a centralized citation management system that tracks all sources
referenced during research and generates proper markdown footnote citations.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum


class SourceType(Enum):
    """Types of sources that can be cited."""
    WEB = "web"
    ACADEMIC = "academic"
    SOCIAL = "social"
    FORUM = "forum"
    NEWS = "news"
    DOCUMENTATION = "documentation"
    OTHER = "other"


@dataclass
class Source:
    """Represents a citable source."""
    id: int
    title: str
    url: str
    source_type: SourceType
    authors: List[str] = field(default_factory=list)
    date: Optional[str] = None
    accessed_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    metadata: Dict[str, Any] = field(default_factory=dict)
    credibility_score: float = 0.0
    
    def __post_init__(self):
        """Validate source data after initialization."""
        if not self.title:
            raise ValueError("Source title is required")
        if not self.url:
            raise ValueError("Source URL is required")


def calculate_credibility_score(source_type: SourceType, metadata: Dict[str, Any]) -> float:
    """
    Calculate a credibility score for a source based on type and metadata.
    
    Scoring factors:
    - Base score by source type (academic highest, social lowest)
    - Authority indicators (authors, peer review, citations)
    - Freshness (publication date)
    
    Returns:
        A score from 0.0 to 1.0 representing source credibility
    """
    # Base scores by source type
    base_scores = {
        SourceType.ACADEMIC: 0.8,
        SourceType.DOCUMENTATION: 0.7,
        SourceType.NEWS: 0.6,
        SourceType.WEB: 0.5,
        SourceType.FORUM: 0.4,
        SourceType.SOCIAL: 0.3,
        SourceType.OTHER: 0.3,
    }
    
    score = base_scores.get(source_type, 0.3)
    
    # Boost for having authors identified
    if metadata.get("authors"):
        score += 0.1
    
    # Boost for peer-reviewed sources
    if metadata.get("peer_reviewed"):
        score += 0.1
    
    # Boost for community engagement (social/forum sources)
    # Higher engagement indicates more community vetting
    if source_type in (SourceType.SOCIAL, SourceType.FORUM):
        upvotes = metadata.get("upvotes", 0) or 0
        comments = metadata.get("num_comments", 0) or 0
        if upvotes > 100 or comments > 50:
            score += 0.15
        elif upvotes > 50 or comments > 20:
            score += 0.1
    
    # Cap at 1.0
    return min(score, 1.0)


class CitationManager:
    """
    Manages citations for research sources.
    
    Tracks all sources referenced during research and generates markdown
    footnote citations in the format [^n] where n is the citation number.
    
    Example:
        >>> manager = CitationManager()
        >>> manager.add_source(
        ...     title="Python Documentation",
        ...     url="https://docs.python.org/3/",
        ...     source_type=SourceType.DOCUMENTATION
        ... )
        >>> manager.generate_citation(1)
        '[^1]'
        >>> manager.compile_bibliography()
        '[^1]: Python Documentation. https://docs.python.org/3/. Accessed: 2024-01-15'
    """
    
    def __init__(self):
        """Initialize the citation manager."""
        self._sources: Dict[int, Source] = {}
        self._next_id: int = 1
        self._source_urls: Dict[str, int] = {}  # URL to ID mapping for deduplication
    
    def add_source(
        self,
        title: str,
        url: str,
        source_type: SourceType,
        authors: Optional[List[str]] = None,
        date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Register a new source and return its citation ID.
        
        If a source with the same URL already exists, returns the existing ID
        instead of creating a duplicate.
        
        Args:
            title: The title of the source
            url: The URL of the source
            source_type: The type of source (web, academic, social, etc.)
            authors: Optional list of authors
            date: Optional publication date
            metadata: Optional additional metadata (e.g., score, votes, etc.)
            
        Returns:
            The citation ID for this source
            
        Example:
            >>> manager.add_source(
            ...     title="Example Article",
            ...     url="https://example.com/article",
            ...     source_type=SourceType.WEB,
            ...     authors=["John Doe", "Jane Smith"],
            ...     date="2024-01-15"
            ... )
            1
        """
        # Check for duplicate URLs
        if url in self._source_urls:
            return self._source_urls[url]

        # Coerce string → enum at the boundary. The type hint says
        # `SourceType` but several call sites in core/tools.py pass
        # plain strings ("web", "academic", ...) — those used to land
        # in storage verbatim and crash any consumer that did
        # `source.source_type.value`. Normalize here so every Source
        # in storage carries a proper enum, regardless of caller.
        if isinstance(source_type, str):
            try:
                source_type = SourceType(source_type.lower())
            except ValueError:
                source_type = SourceType.OTHER
        elif not isinstance(source_type, SourceType):
            source_type = SourceType.OTHER

        citation_id = self._next_id

        # Calculate credibility score
        credibility_score = calculate_credibility_score(
            source_type,
            metadata or {}
        )

        source = Source(
            id=citation_id,
            title=title,
            url=url,
            source_type=source_type,
            authors=authors or [],
            date=date,
            metadata=metadata or {},
            credibility_score=credibility_score
        )
        
        self._sources[citation_id] = source
        self._source_urls[url] = citation_id
        self._next_id += 1
        
        return citation_id
    
    def get_source(self, citation_id: int) -> Optional[Source]:
        """
        Get a source by its citation ID.
        
        Args:
            citation_id: The citation ID to look up
            
        Returns:
            The Source object if found, None otherwise
        """
        return self._sources.get(citation_id)
    
    def generate_citation(self, citation_id: int) -> str:
        """
        Generate a markdown footnote citation reference.
        
        Args:
            citation_id: The citation ID to reference
            
        Returns:
            A markdown footnote reference in the format [^n]
            
        Raises:
            ValueError: If the citation_id does not exist
            
        Example:
            >>> manager.generate_citation(1)
            '[^1]'
        """
        if citation_id not in self._sources:
            raise ValueError(f"Citation ID {citation_id} not found")
        
        return f"[^{citation_id}]"
    
    def _format_authors(self, authors: List[str]) -> str:
        """Format a list of authors for citation."""
        if not authors:
            return ""
        
        if len(authors) == 1:
            return authors[0]
        elif len(authors) == 2:
            return f"{authors[0]} & {authors[1]}"
        else:
            return f"{authors[0]} et al."
    
    def _format_bibliography_entry(self, source: Source) -> str:
        """
        Format a single bibliography entry with credibility indicators.
        
        Args:
            source: The source to format
            
        Returns:
            A formatted bibliography entry string
        """
        parts = [f"[^{source.id}]: {source.title}."]
        
        # Add authors if present
        if source.authors:
            author_str = self._format_authors(source.authors)
            parts[0] = f"[^{source.id}]: {author_str}. {source.title}."
        
        # Add URL
        parts.append(source.url)
        
        # Add date if present
        if source.date:
            parts.append(f"Published: {source.date}.")
        
        # Add accessed date
        parts.append(f"Accessed: {source.accessed_date}.")
        
        # Add source type indicator
        type_indicator = {
            SourceType.ACADEMIC: "[Academic]",
            SourceType.SOCIAL: "[Social]",
            SourceType.FORUM: "[Forum]",
            SourceType.NEWS: "[News]",
            SourceType.DOCUMENTATION: "[Documentation]",
            SourceType.WEB: "[Web]",
            SourceType.OTHER: ""
        }
        
        if type_indicator.get(source.source_type):
            parts.append(type_indicator[source.source_type])
        
        # Add credibility score with stars
        if source.credibility_score > 0:
            stars = "★" * int(round(source.credibility_score * 5))
            stars_empty = "☆" * (5 - int(round(source.credibility_score * 5)))
            parts.append(f"(Credibility: {stars}{stars_empty} {source.credibility_score:.1f}/1.0)")
        
        return " ".join(parts)
    
    def compile_bibliography(self) -> str:
        """
        Compile all cited sources into a bibliography section.
        
        Returns:
            A formatted bibliography section with all sources in citation order
            
        Example:
            >>> manager.compile_bibliography()
            '## Bibliography\\n\\n[^1]: Python Documentation. https://docs.python.org/3/. Accessed: 2024-01-15\\n\\n[^2]: Example Article. John Doe. https://example.com/article. Published: 2024-01-10. Accessed: 2024-01-15'
        """
        if not self._sources:
            return ""
        
        lines = ["## Bibliography", ""]
        
        for citation_id in sorted(self._sources.keys()):
            source = self._sources[citation_id]
            lines.append(self._format_bibliography_entry(source))
            lines.append("")
        
        return "\n".join(lines).strip()
    
    def clear(self) -> None:
        """Clear all stored sources and reset the citation counter."""
        self._sources.clear()
        self._source_urls.clear()
        self._next_id = 1
    
    @property
    def source_count(self) -> int:
        """Return the number of registered sources."""
        return len(self._sources)
    
    def get_all_sources(self) -> List[Source]:
        """Return all registered sources in citation order."""
        return [self._sources[cid] for cid in sorted(self._sources.keys())]


# Global citation manager instance for use across tools
_citation_manager: Optional[CitationManager] = None


def get_citation_manager() -> CitationManager:
    """
    Get the global citation manager instance.
    
    Creates a new instance if one doesn't exist.
    
    Returns:
        The global CitationManager instance
    """
    global _citation_manager
    if _citation_manager is None:
        _citation_manager = CitationManager()
    return _citation_manager


def reset_citation_manager() -> None:
    """Reset the global citation manager to a fresh instance."""
    global _citation_manager
    _citation_manager = CitationManager()


def register_source(
    title: str,
    url: str,
    source_type: SourceType,
    authors: Optional[List[str]] = None,
    date: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    """
    Convenience function to register a source with the global citation manager.
    
    Args:
        title: The title of the source
        url: The URL of the source
        source_type: The type of source
        authors: Optional list of authors
        date: Optional publication date
        metadata: Optional additional metadata
        
    Returns:
        The citation ID for this source
    """
    return get_citation_manager().add_source(
        title=title,
        url=url,
        source_type=source_type,
        authors=authors,
        date=date,
        metadata=metadata
    )


def get_citation(citation_id: int) -> str:
    """
    Convenience function to get a citation reference.
    
    Args:
        citation_id: The citation ID
        
    Returns:
        The markdown footnote reference
    """
    return get_citation_manager().generate_citation(citation_id)


def compile_bibliography() -> str:
    """
    Convenience function to compile the bibliography.
    
    Returns:
        The formatted bibliography section
    """
    return get_citation_manager().compile_bibliography()