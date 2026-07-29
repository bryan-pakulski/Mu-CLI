"""Docker-backed MuCLI session runtime.

The package intentionally keeps imports lazy.  The unprivileged egress proxy
runs with ``python -m mu.container.egress_proxy`` inside a read-only container;
eagerly importing the supervisor/registry stack would initialise normal MuCLI
state under ``$MUCLI_HOME`` before the proxy can bind its socket.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
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

_LAZY_EXPORTS = {
    "ContainerRef": ("mu.container.ref", "ContainerRef"),
    "MountSpec": ("mu.container.ref", "MountSpec"),
    "ContainerRegistry": ("mu.container.registry", "ContainerRegistry"),
    "ContainerSupervisor": ("mu.container.supervisor", "ContainerSupervisor"),
    "ContainerTemplate": ("mu.container.templates", "ContainerTemplate"),
    "TemplateRegistry": ("mu.container.templates", "TemplateRegistry"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    module = __import__(module_name, fromlist=[attribute])
    value = getattr(module, attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
