Mu-CLI Is a provider-neutral agent harness.

The goal of Mu-CLI is to provide a flexible and extensible framework for running agents.

## Core Concepts
- Agents are **long-running** processes that run in a **sandboxed** workspace.
- Agents have access to **tools** and **skills** that are **dynamically discovered** at runtime.
- Agents have access to **context** and **memory** that is **persisted** across runs in a **workspace** and maintained in real time.
- Agents have access to different frameworks that dictate their **execution** behavior i.e. Research Mode v.s Interactive Mode v.s. Debugging Mode v.s YOLO mode etc..
- Agent actions are visible and **auditable** all actions are **logged** and **traced**.


- The core of Mu-CLI should be a server that exposes a API for CLI / GUI's to interact with.
- The server should maintain a session manager which allows for **persistance**, **resumption**, **multi-session** support

- Sessions contain **context** and **memory** that is **persisted** across runs in a **workspace**.
- Sessions support streaming responses and long-running jobs with a **job lifecycle** and **job state**.
- Sessions support an agent loop that is **customizable** and **extensible**

- There is multi provider support for **LLMS**, the core first class citizen should be OLLAMA
- We should support THINKING, STREAMING OUTPUT, TOOL CALLS and SKILLS
- PROVIDERS should be pluggable and customizable

## Deliverables
Mu-CLI Server
Mu-CLI CLI
Mu-CLI GUI
