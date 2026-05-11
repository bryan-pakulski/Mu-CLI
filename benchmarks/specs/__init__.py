"""Built-in benchmark specifications, one canonical example per mode.

Each submodule exports a list named `SPECS` containing `BenchmarkSpec`
instances. `ALL_SPECS` here is the union — used by the CLI's `list`
and `run` subcommands.
"""

from typing import List

from ..spec import BenchmarkSpec
from . import debug_specs
from . import default_specs
from . import feature_specs
from . import loop_specs
from . import research_specs


ALL_SPECS: List[BenchmarkSpec] = (
    default_specs.SPECS
    + debug_specs.SPECS
    + feature_specs.SPECS
    + research_specs.SPECS
    + loop_specs.SPECS
)


def find(name: str) -> BenchmarkSpec:
    """Look up a spec by name. Raises KeyError if not found."""
    for s in ALL_SPECS:
        if s.name == name:
            return s
    raise KeyError(f"benchmark spec not found: {name!r}")


def by_mode(mode: str) -> List[BenchmarkSpec]:
    return [s for s in ALL_SPECS if s.mode == mode]


__all__ = ["ALL_SPECS", "by_mode", "find"]
