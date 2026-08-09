"""TUI model-economics baseline."""

from __future__ import annotations

from typing import Any

from utils.model_pricing import pricing_catalog

from . import CommandResult, command


def _money(value) -> str:
    return "—" if value is None else f"${float(value):.3f}"


def _emit(session: Any, text: str, allow_prompt: bool) -> None:
    if not allow_prompt:
        return
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_info"):
        ui.show_info(text)


@command(
    "/costs",
    "/pricing",
    help="Show MuCLI's versioned model cost baseline. Optional: /costs openai|gemini|ollama",
)
def costs_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    catalog = pricing_catalog()
    wanted = str(args or "").strip().lower()
    if wanted and wanted not in {"openai", "gemini", "ollama"}:
        return CommandResult(
            ok=False,
            message="Usage: /costs [openai|gemini|ollama]",
        )

    lines = [
        f"Model cost baseline · {catalog['version']} · USD / 1M tokens",
        "Input / cached input / output",
        "",
    ]
    if wanted in {"", "openai", "gemini"}:
        for provider in ("openai", "gemini"):
            if wanted and wanted != provider:
                continue
            lines.append(provider.upper())
            for item in catalog["models"]:
                if item["provider"] != provider:
                    continue
                normal = (
                    f"{_money(item['input_per_million'])} / "
                    f"{_money(item['cached_input_per_million'])} / "
                    f"{_money(item['output_per_million'])}"
                )
                tier = ""
                if item.get("long_context_cutoff"):
                    tier = f" · high tier >{int(item['long_context_cutoff']):,} input"
                lines.append(f"  {item['key']:<28} {normal}{tier}")
            lines.append("")

    if wanted in {"", "ollama"}:
        lines.extend([
            "OLLAMA",
            "  local daemon                 $0 provider/API cost · host/GPU compute excluded",
            "  Ollama Cloud                 plan/usage based · no fabricated token rate",
        ])
        for item in catalog["ollama"]:
            meta = " · ".join(
                value for value in (
                    item.get("local_size") or "",
                    f"ctx {int(item['context_window']):,}" if item.get("context_window") else "",
                    item.get("usage_tier") or "",
                ) if value
            )
            lines.append(f"  {item['key']:<28} {item.get('role') or ''}{(' · ' + meta) if meta else ''}")
        lines.append("")

    lines.append("These are planning/telemetry estimates, not provider invoices.")
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message="Model pricing baseline generated.", data=catalog)
