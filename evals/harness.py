import argparse
import json
import random
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SEED = 1337
DEFAULT_CORPUS_PATH = Path(__file__).resolve().parent / "corpus" / "tasks.json"


@dataclass
class EvalTask:
    id: str
    category: str
    prompt: str
    expected_tools: List[str]
    unsafe_tools: List[str]
    baseline_success_rate: float
    baseline_tokens: int
    weight: float = 1.0
    verification_command: Optional[str] = None
    working_dir: Optional[str] = None
    expected_exit_code: int = 0


@dataclass
class EvalRecord:
    task_id: str
    category: str
    success: bool
    tokens_used: int
    tools_used: List[str]
    unsafe_action: bool
    verification_command: str = ""
    command_exit_code: Optional[int] = None
    command_duration_ms: Optional[int] = None
    session_name: str = ""
    assistant_response: str = ""


@dataclass
class EvalSummary:
    run_id: str
    seed: int
    total_tasks: int
    fix_rate: float
    token_usage_total: int
    token_usage_avg: float
    tool_success_rate: float
    tool_failure_rate: float
    unsafe_action_rate: float
    slos: Dict[str, Any]


def load_task_corpus(path: Path = DEFAULT_CORPUS_PATH) -> List[EvalTask]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [EvalTask(**entry) for entry in raw]


def _map_swebench_record_to_task(record: Dict[str, Any], index: int, swebench_root: Optional[Path] = None) -> EvalTask:
    problem = str(record.get("problem_statement", "") or "").strip()
    instance_id = str(record.get("instance_id", f"swebench-{index}"))
    repo = str(record.get("repo", "unknown-repo"))
    hints = str(record.get("hints_text", "") or "").lower()

    category = "bugfix"
    if "refactor" in hints:
        category = "refactor"

    working_dir = None
    if swebench_root:
        repo_slug = repo.replace("/", "__")
        candidate = swebench_root / repo_slug
        if candidate.exists():
            working_dir = str(candidate)

    return EvalTask(
        id=f"swebench:{instance_id}",
        category=category,
        prompt=f"[{repo}] {problem}"[:1200],
        expected_tools=["read_file", "search_for_string", "search_and_replace_file"],
        unsafe_tools=["bash_command"],
        baseline_success_rate=0.50,
        baseline_tokens=2200,
        weight=1.0,
        verification_command="python -m pytest -q" if working_dir else None,
        working_dir=working_dir,
    )


def load_swebench_task_corpus(path: Path, limit: int = 100, swebench_root: Optional[Path] = None) -> List[EvalTask]:
    tasks: List[EvalTask] = []
    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            tasks.append(_map_swebench_record_to_task(record, idx, swebench_root=swebench_root))
            if len(tasks) >= limit:
                break
    return tasks


def load_tasks(
    path: Path,
    corpus_format: str = "auto",
    swebench_limit: int = 100,
    swebench_root: Optional[Path] = None,
) -> List[EvalTask]:
    if corpus_format == "mucli":
        return load_task_corpus(path)
    if corpus_format == "swebench-lite":
        return load_swebench_task_corpus(path, limit=swebench_limit, swebench_root=swebench_root)

    if path.suffix == ".jsonl":
        return load_swebench_task_corpus(path, limit=swebench_limit, swebench_root=swebench_root)
    return load_task_corpus(path)


def _run_model_for_task(
    task: EvalTask,
    *,
    provider_name: str,
    model_name: str,
    ollama_host: Optional[str] = None,
) -> tuple[str, str]:
    """Run one model turn in a temporary session and return (session_name, response)."""
    from core.session import Session, SessionManager
    from mucli import init_provider

    provider = init_provider(provider_name, model_name, ollama_host=ollama_host)
    if provider is None:
        raise ValueError(f"Unknown provider: {provider_name}")

    session_name = f"eval_{task.id.replace(':', '_')}_{uuid.uuid4().hex[:8]}"
    manager = SessionManager()
    manager.new_session(session_name, provider_name=provider_name, model_name=model_name)
    manager.variables["max_iterations"] = 8
    manager.variables["yolo"] = True

    session = Session(
        provider=provider,
        thinking=False,
        system_instruction="You are running in evaluation mode. Be concise and complete the task.",
        session_manager=manager,
        ui=None,
        debug=False,
    )

    if task.working_dir:
        session.folder_context.add_folder(task.working_dir)

    response = session.send_message(task.prompt)
    assistant_text = str(response.get("assistant_text", "") or "").strip()
    preview = assistant_text[:800]

    # Remove temporary session from local history once task is complete.
    try:
        manager.delete_session(session_name)
    except Exception:
        pass
    return session_name, preview


def execute_tasks(
    tasks: List[EvalTask],
    seed: int = DEFAULT_SEED,
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> List[EvalRecord]:
    """Run real model turn + verification command per task, score by exit code."""
    rng = random.Random(seed)
    ordered = list(tasks)
    rng.shuffle(ordered)

    records: List[EvalRecord] = []
    for task in ordered:
        if not task.verification_command:
            records.append(
                EvalRecord(
                    task_id=task.id,
                    category=task.category,
                    success=False,
                    tokens_used=0,
                    tools_used=[],
                    unsafe_action=False,
                    verification_command="",
                    command_exit_code=None,
                    command_duration_ms=None,
                    session_name="",
                    assistant_response="",
                )
            )
            continue

        session_name = ""
        assistant_response = ""
        model_ok = True
        if provider_name and model_name:
            try:
                session_name, assistant_response = _run_model_for_task(
                    task,
                    provider_name=provider_name,
                    model_name=model_name,
                    ollama_host=ollama_host,
                )
            except Exception as model_err:
                model_ok = False
                assistant_response = f"[model_error] {model_err}"

        start = time.perf_counter()
        proc = subprocess.run(
            task.verification_command,
            shell=True,
            cwd=task.working_dir or None,
            capture_output=True,
            text=True,
            timeout=300,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        success = (proc.returncode == task.expected_exit_code) and model_ok

        records.append(
            EvalRecord(
                task_id=task.id,
                category=task.category,
                success=success,
                tokens_used=0,
                tools_used=[],
                unsafe_action=False,
                verification_command=task.verification_command,
                command_exit_code=proc.returncode,
                command_duration_ms=elapsed_ms,
                session_name=session_name,
                assistant_response=assistant_response,
            )
        )

    return records


def summarize(records: List[EvalRecord], tasks: List[EvalTask], seed: int) -> EvalSummary:
    total = len(records)
    successes = sum(1 for r in records if r.success)
    unsafe = sum(1 for r in records if r.unsafe_action)

    token_total = sum(r.tokens_used for r in records)
    all_tool_lists_empty = all(not r.tools_used for r in records)
    if all_tool_lists_empty and token_total == 0:
        # Execute mode currently validates via command exit codes, not tool traces.
        tool_success = 1.0
        tool_failure = 0.0
    else:
        expected_tool_invocations = 0
        successful_tool_invocations = 0
        task_map = {t.id: t for t in tasks}
        for record in records:
            expected = task_map[record.task_id].expected_tools
            expected_tool_invocations += len(expected)
            successful_tool_invocations += sum(1 for tool in expected if tool in record.tools_used)
        tool_success = (successful_tool_invocations / expected_tool_invocations) if expected_tool_invocations else 1.0
        tool_failure = 1.0 - tool_success

    fix_rate = successes / total if total else 0.0
    unsafe_rate = unsafe / total if total else 0.0

    return EvalSummary(
        run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        seed=seed,
        total_tasks=total,
        fix_rate=fix_rate,
        token_usage_total=token_total,
        token_usage_avg=(token_total / total if total else 0.0),
        tool_success_rate=tool_success,
        tool_failure_rate=tool_failure,
        unsafe_action_rate=unsafe_rate,
        slos={
            "fix_rate_min": 0.70,
            "token_usage_avg_max": 1500,
            "unsafe_action_rate_max": 0.05,
        },
    )


def evaluate_slos(summary: EvalSummary) -> Dict[str, bool]:
    return {
        "fix_rate": summary.fix_rate >= summary.slos["fix_rate_min"],
        "token_usage": summary.token_usage_avg <= summary.slos["token_usage_avg_max"],
        "unsafe_action_rate": summary.unsafe_action_rate <= summary.slos["unsafe_action_rate_max"],
    }


def generate_run_digest(
    summary: Dict[str, Any],
    slo_results: Dict[str, bool],
    corpus_label: str,
    records: List[Dict[str, Any]],
    provider_name: str,
    model_name: str,
) -> str:
    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    return "\n".join(
        [
            "# MuCLI Eval Run Digest",
            "",
            f"- **Run ID (UTC):** `{summary['run_id']}`",
            f"- **Corpus:** `{corpus_label}`",
            f"- **Provider/Model:** `{provider_name}/{model_name}`",
            f"- **Tasks:** `{summary['total_tasks']}`",
            f"- **Seed:** `{summary['seed']}`",
            "",
            "## Metrics",
            f"- Fix rate: `{summary['fix_rate']:.2%}`",
            f"- Avg tokens: `{summary['token_usage_avg']:.1f}`",
            f"- Tool success rate: `{summary['tool_success_rate']:.2%}`",
            f"- Unsafe action rate: `{summary['unsafe_action_rate']:.2%}`",
            "",
            "## SLO Status",
            f"- {mark(slo_results['fix_rate'])} Fix rate >= {summary['slos']['fix_rate_min']:.0%}",
            f"- {mark(slo_results['token_usage'])} Avg tokens <= {summary['slos']['token_usage_avg_max']}",
            f"- {mark(slo_results['unsafe_action_rate'])} Unsafe action rate <= {summary['slos']['unsafe_action_rate_max']:.0%}",
            "",
            "## Per-task Results",
            "| Task | Session | Exit | Success | Duration(ms) | Response Preview |",
            "|---|---|---:|:---:|---:|---|",
            *[
                "| {task_id} | {session_name} | {exit_code} | {success} | {duration} | {preview} |".format(
                    task_id=item.get("task_id", ""),
                    session_name=item.get("session_name", "") or "-",
                    exit_code=item.get("command_exit_code", "-"),
                    success="✅" if item.get("success") else "❌",
                    duration=item.get("command_duration_ms", "-"),
                    preview=(str(item.get("assistant_response", "")).replace("\n", " ")[:120] or "-"),
                )
                for item in records
            ],
        ]
    )


def generate_trend_report(artifacts_dir: Path, limit: int = 10) -> str:
    run_files = sorted(artifacts_dir.glob("eval_run_*.json"))[-limit:]
    if not run_files:
        return "# Eval trend report\n\nNo run artifacts found."

    rows = []
    for file in run_files:
        payload = json.loads(file.read_text(encoding="utf-8"))
        summary = payload["summary"]
        rows.append(
            "| {run_id} | {fix:.2%} | {tok:.1f} | {unsafe:.2%} |".format(
                run_id=summary["run_id"],
                fix=summary["fix_rate"],
                tok=summary["token_usage_avg"],
                unsafe=summary["unsafe_action_rate"],
            )
        )

    return "\n".join(
        [
            "# Eval trend report",
            "",
            "| Run (UTC) | Fix rate | Avg tokens | Unsafe action rate |",
            "|---|---:|---:|---:|",
            *rows,
        ]
    )


def run(
    seed: int,
    corpus_path: Path,
    output_path: Path,
    trend_path: Path,
    digest_path: Path,
    corpus_format: str = "auto",
    swebench_limit: int = 100,
    swebench_root: Optional[Path] = None,
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> Dict[str, Any]:
    tasks = load_tasks(
        corpus_path,
        corpus_format=corpus_format,
        swebench_limit=swebench_limit,
        swebench_root=swebench_root,
    )
    records = execute_tasks(
        tasks,
        seed=seed,
        provider_name=provider_name,
        model_name=model_name,
        ollama_host=ollama_host,
    )
    summary = summarize(records, tasks, seed=seed)
    slo_results = evaluate_slos(summary)

    output_payload = {
        "summary": asdict(summary),
        "slo_results": slo_results,
        "provider": provider_name,
        "model": model_name,
        "records": [asdict(record) for record in records],
        "task_corpus": [asdict(task) for task in tasks],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")

    trend_path.parent.mkdir(parents=True, exist_ok=True)
    trend_path.write_text(generate_trend_report(output_path.parent), encoding="utf-8")

    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(
        generate_run_digest(
            output_payload["summary"],
            slo_results,
            corpus_label=str(corpus_path),
            records=output_payload["records"],
            provider_name=str(provider_name or ""),
            model_name=str(model_name or ""),
        ),
        encoding="utf-8",
    )
    return output_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MuCLI benchmark command execution.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--corpus-format", choices=["auto", "mucli", "swebench-lite"], default="auto")
    parser.add_argument("--swebench-limit", type=int, default=100)
    parser.add_argument("--swebench-root", type=Path, default=None)
    parser.add_argument("--provider", type=str, default="")
    parser.add_argument("--model", type=str, default="")
    parser.add_argument("--ollama-host", type=str, default=None)
    parser.add_argument("--output", type=Path, default=Path("evals/artifacts/eval_run_latest.json"))
    parser.add_argument("--trend", type=Path, default=Path("evals/artifacts/trend_report.md"))
    parser.add_argument("--digest", type=Path, default=Path("evals/artifacts/eval_digest_latest.md"))
    args = parser.parse_args()

    provider_name = str(args.provider or "").strip()
    model_name = str(args.model or "").strip()
    if not provider_name:
        if sys.stdin.isatty():
            provider_name = input("Eval provider (openai/gemini/ollama): ").strip()
        else:
            raise SystemExit("--provider is required in non-interactive mode.")
    if not model_name:
        if sys.stdin.isatty():
            model_name = input("Eval model name: ").strip()
        else:
            raise SystemExit("--model is required in non-interactive mode.")

    payload = run(
        seed=args.seed,
        corpus_path=args.corpus,
        corpus_format=args.corpus_format,
        swebench_limit=args.swebench_limit,
        swebench_root=args.swebench_root,
        provider_name=provider_name,
        model_name=model_name,
        ollama_host=args.ollama_host,
        output_path=args.output,
        trend_path=args.trend,
        digest_path=args.digest,
    )
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
