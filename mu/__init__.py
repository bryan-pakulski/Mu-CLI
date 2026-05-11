"""mu: clean re-architecture of the Mu-CLI core.

This package is being built incrementally alongside the legacy `core/`,
`providers/`, and `mucli.py` modules. See
/root/.claude/plans/review-this-local-harness-lazy-lynx.md for the
migration plan. Until cutover, importing from `mu.*` is preferred for
new code; legacy callsites keep working through the old modules.
"""

__all__: list = []
