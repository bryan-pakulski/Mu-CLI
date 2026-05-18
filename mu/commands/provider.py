"""Provider / model slash commands."""

from typing import Any

from . import CommandResult, command


def _emit(session: Any, body: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if ui is None or not allow_prompt:
        return
    method = "show_error" if error else "show_info"
    if hasattr(ui, method):
        getattr(ui, method)(body)


def _console(session: Any):
    ui = getattr(session, "ui", None)
    return getattr(ui, "console", None) if ui is not None else None


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, getattr(session, "ui", None))
    except ImportError:
        pass


@command(
    "/model",
    help="Show or change the active model. Interactive picker if no arg.",
)
def model_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    arg = (args or "").strip()
    if not arg:
        if not allow_prompt:
            return CommandResult(
                ok=False, message="Non-interactive mode requires /model <name>."
            )
        models = session.provider.get_available_models()
        if not models:
            return CommandResult(ok=False, message="No models available.")
        console = _console(session)
        if console is not None:
            try:
                from rich.prompt import IntPrompt

                console.print("\n[bold cyan]Available Models:[/bold cyan]")
                for i, model in enumerate(models, 1):
                    console.print(f" {i}. {model}", markup=False)
                choice = IntPrompt.ask(
                    "Select a model",
                    choices=[str(i) for i in range(1, len(models) + 1)],
                )
                arg = models[int(choice) - 1]
            except Exception as exc:
                return CommandResult(ok=False, message=f"Model selection failed: {exc}")
        else:
            return CommandResult(
                ok=False, message="No console for interactive model selection."
            )

    session.provider.model_name = arg.strip()
    session.session_manager.provider_config = {
        "provider": session.provider.name,
        "model": session.provider.model_name,
    }
    session.session_manager.save_history()
    _emit(session, f"Model changed to: {session.provider.model_name}", allow_prompt)
    _refresh_hud(session)
    return CommandResult(
        ok=True,
        message=f"Model changed to {session.provider.model_name}.",
        data={"model": session.provider.model_name},
    )


@command(
    "/provider",
    help="Switch provider (openai|gemini|ollama). Interactive picker if no arg.",
)
def provider_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    arg = (args or "").strip()
    if not arg and not allow_prompt:
        return CommandResult(
            ok=False, message="Non-interactive mode requires /provider <name>."
        )
    try:
        from mucli import print_splash, select_provider_and_model

        ollama_host = session.variables.get("ollama_host")
        session.provider = select_provider_and_model(
            arg if arg else None,
            session.provider.model_name if not allow_prompt else None,
            ollama_host=ollama_host,
            allow_prompt=allow_prompt,
        )
        session.session_manager.provider_config = {
            "provider": session.provider.name,
            "model": session.provider.model_name,
        }
        session.session_manager.save_history()
        _emit(session, "Provider changed successfully!", allow_prompt)
        if allow_prompt:
            try:
                print_splash(session)
            except Exception:
                pass
        _refresh_hud(session)
        return CommandResult(
            ok=True,
            message="Provider changed successfully.",
            data={
                "provider": session.provider.name,
                "model": session.provider.model_name,
            },
        )
    except ImportError:
        return CommandResult(
            ok=False, message="Provider switching requires the mucli runtime."
        )
    except Exception as exc:
        _emit(session, f"Failed to change provider: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))
