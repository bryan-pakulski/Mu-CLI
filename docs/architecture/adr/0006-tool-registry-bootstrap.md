# ADR 0006: Tool Registry Bootstrap for Phase 3

## Status
Accepted

## Context
Tool support is required early in Phase 3, but full plugin discovery and sandbox integrations are still evolving.

## Decision
Start with an in-process tool registry containing typed tool definitions (name, risk level, approval requirement), exposed via API.

Initial tools are capability descriptors used for policy and orchestration flow:
- `workspace.read_file`
- `workspace.write_file`
- `shell.exec`

## Consequences
- Pros: unblocks policy/approval flows without waiting for full tool execution runtime.
- Pros: creates a stable API contract for CLI/GUI integration.
- Cons: execution backends for tools remain partial.
- Mitigation: evolve registry to dynamic discovery with executable backends in subsequent phase increments.
