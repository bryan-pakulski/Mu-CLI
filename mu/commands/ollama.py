"""Ollama-specific slash commands.

  /ollama                        — show status (host, daemon up?, models)
  /ollama status                 — same as bare /ollama
  /ollama models                 — list installed models
  /ollama pull <model>           — `ollama pull <model>` streamed via bash
  /ollama options                — show current ollama_* session variables
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from . import CommandResult, command


_OPTION_KEYS = (
    "num_ctx",
    "num_predict",
    "temperature",
    "top_p",
    "top_k",
    "repeat_penalty",
    "seed",
    "mirostat",
)


def _get_provider(session: Any):
    """Return the OllamaProvider if the session is currently using Ollama, else None."""
    provider = getattr(session, "provider", None)
    if provider is None:
        return None
    if getattr(provider, "name", "") != "ollama":
        return None
    return provider


def _status_subcommand(session: Any) -> CommandResult:
    provider = _get_provider(session)
    if provider is None:
        return CommandResult(
            ok=False,
            message="Current session is not using the Ollama provider. /provider ollama to switch.",
        )
    try:
        provider.invalidate_preflight()
        provider.preflight()
        models = provider.get_available_models()
    except Exception as exc:  # OllamaError or any
        message = getattr(exc, "actionable", str(exc))
        if hasattr(session, "ui") and session.ui is not None and hasattr(session.ui, "show_error"):
            try:
                session.ui.show_error(f"⚠️ Ollama: {message}")
            except Exception:
                pass
        return CommandResult(
            ok=False,
            message=message,
            data={"host": getattr(provider, "host", "?")},
        )

    current_model = provider.model_name or "(none selected — /model <name> to pick)"
    installed = ", ".join(models) if models else "(none)"
    body = (
        f"Ollama daemon: ✓ reachable at {provider.host}\n"
        f"Current model: {current_model}\n"
        f"Installed models ({len(models)}): {installed}"
    )
    if hasattr(session, "ui") and session.ui is not None and hasattr(session.ui, "show_info"):
        try:
            session.ui.show_info(body)
        except Exception:
            pass
    return CommandResult(
        ok=True,
        message=body,
        data={
            "host": provider.host,
            "model": provider.model_name,
            "models": models,
        },
    )


def _models_subcommand(session: Any) -> CommandResult:
    provider = _get_provider(session)
    if provider is None:
        return CommandResult(
            ok=False,
            message="Current session is not using the Ollama provider.",
        )
    models = provider.get_available_models()
    body = "\n".join(f"  - {m}" for m in models) if models else "  (none installed)"
    if session.ui and hasattr(session.ui, "show_info"):
        try:
            session.ui.show_info(f"Installed Ollama models ({len(models)}):\n{body}")
        except Exception:
            pass
    return CommandResult(ok=True, message=body, data={"models": models})


def _pull_subcommand(session: Any, model_arg: str) -> CommandResult:
    if not model_arg:
        return CommandResult(
            ok=False,
            message="Usage: /ollama pull <model>",
        )
    cmd = ["ollama", "pull", model_arg]
    if session.ui and hasattr(session.ui, "show_info"):
        try:
            session.ui.show_info(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
        except Exception:
            pass
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except FileNotFoundError:
        return CommandResult(
            ok=False,
            message="`ollama` CLI not found on PATH. Install it from https://ollama.com/download.",
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            ok=False,
            message="`ollama pull` timed out after 10 minutes. Try the CLI directly to monitor.",
        )

    # Invalidate the provider's cached model list so subsequent calls see the new tag.
    provider = _get_provider(session)
    if provider is not None and hasattr(provider, "invalidate_preflight"):
        provider.invalidate_preflight()

    body = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0:
        return CommandResult(
            ok=False,
            message=f"ollama pull failed (exit {proc.returncode}):\n{body[:600]}",
        )
    if session.ui and hasattr(session.ui, "show_info"):
        try:
            session.ui.show_info(f"✓ Pulled {model_arg}")
        except Exception:
            pass
    return CommandResult(
        ok=True,
        message=f"✓ Pulled {model_arg}",
        data={"model": model_arg, "stdout": body},
    )


def _options_subcommand(session: Any) -> CommandResult:
    """Show the current ollama_* session variables that the provider reads."""
    rows = []
    for key in _OPTION_KEYS:
        var_key = f"ollama_{key}"
        val = session.variables.get(var_key) if hasattr(session, "variables") else None
        rows.append(f"  {var_key}: {val if val is not None else '(default)'}")
    body = "Ollama option overrides:\n" + "\n".join(rows) + (
        "\n\nSet via `/set ollama_num_ctx 16384` etc."
    )
    if session.ui and hasattr(session.ui, "show_info"):
        try:
            session.ui.show_info(body)
        except Exception:
            pass
    return CommandResult(ok=True, message=body)


@command(
    "/ollama",
    help="Ollama-specific helpers: status | models | pull <model> | options",
)
def ollama_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    tokens = (args or "").strip().split(None, 1)
    if not tokens:
        return _status_subcommand(session)
    sub = tokens[0].lower()
    rest = tokens[1] if len(tokens) > 1 else ""
    if sub == "status":
        return _status_subcommand(session)
    if sub == "models":
        return _models_subcommand(session)
    if sub == "pull":
        return _pull_subcommand(session, rest.strip())
    if sub == "options":
        return _options_subcommand(session)
    return CommandResult(
        ok=False,
        message=(
            f"Unknown subcommand {sub!r}. Use: /ollama [status|models|pull <model>|options]"
        ),
    )
