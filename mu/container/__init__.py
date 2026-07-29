"""Docker-backed MuCLI session runtime.

The package has no import-time Docker side effects.  Docker commands run only when a container session is explicitly created or resumed.
"""

from .ref import ContainerRef, MountSpec
from .registry import ContainerRegistry
from .supervisor import ContainerSupervisor
from .templates import ContainerTemplate, TemplateRegistry

__all__ = [
    "ContainerRef",
    "MountSpec",
    "ContainerRegistry",
    "ContainerSupervisor",
    "ContainerTemplate",
    "TemplateRegistry",
]
