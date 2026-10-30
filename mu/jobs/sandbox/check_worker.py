"""Execute one allow-listed suite inside the job runtime, without a shell."""
import json
import shlex
import subprocess
import time
import threading
from pathlib import Path


class LivePipe:
    """Bounded reads and live output; neither logs nor a long line grow unbounded."""
    lock = threading.Lock()

    def __init__(self, pipe, stream, suite=None):
        self.pipe, self.stream, self.sent, self.suite = pipe, stream, 0, suite

    def readline(self):
        chunk = self.pipe.readline(4096)
        if chunk and self.sent < 512 * 1024:
            with self.lock:
                event = {"event": "check_output", "stream": self.stream, "text": chunk}
                if self.suite is not None:
                    event["suite_index"] = self.suite
                print(json.dumps(event), flush=True)
            self.sent += len(chunk)
        return chunk

    def close(self):
        self.pipe.close()


def main():
    from .egress import start_container_proxy
    start_container_proxy()
    request = json.loads(Path("/artifacts/request.json").read_text())
    if isinstance(request.get("suites"), list):
        # Several suites of one build environment share this container (one start-up
        # instead of one per suite); each writes its own result file.
        for index, spec in enumerate(request["suites"]):
            result = run_one({"timeout": request.get("timeout"), **spec}, suite=index)
            Path(f"/output/result-{index}.json").write_text(json.dumps(result))
        return
    Path("/output/result.json").write_text(json.dumps(run_one(request)))


def run_one(request, suite=None):
    from mu.jobs.verification import _CappedReader, _kill_process_group
    started = time.monotonic()
    # run_in_environment commands are agent shell commands (the agent already has a shell in its
    # own container); configured check suites stay shell-less.
    argv = ["/bin/sh", "-c", request["command"]] if request.get("shell") else shlex.split(request["command"])
    junit = request.get("junit")
    default_junit = f"/output/junit-{suite}.xml" if suite is not None else "/output/junit.xml"
    if not request.get("shell") and not junit and (
            Path(argv[0]).name == "pytest" or argv[:3] in (["python", "-m", "pytest"], ["python3", "-m", "pytest"])):
        junit = default_junit
        argv.append("--junitxml=" + junit)
    # A relative cwd is inside the primary source; /sources/<name>/... runs in an extra source.
    cwd = Path("/workspace") / str(request.get("cwd") or "")
    inside = any(cwd.resolve().is_relative_to(Path(root)) for root in ("/workspace", "/sources"))
    if not inside or cwd.resolve() == Path("/sources") or not cwd.is_dir():
        return {"passed": False, "return_code": 2, "timed_out": False, "stdout": "",
                "stderr": f"working directory {request.get('cwd')!r} does not exist in the repository"}
    try:
        child = subprocess.Popen(argv, cwd=str(cwd), shell=False,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    except (FileNotFoundError, PermissionError) as exc:
        # A missing toolchain is a check result the host can explain, not a crash.
        return {"passed": False, "return_code": 127, "timed_out": False, "stdout": "",
                "stderr": f"{argv[0]}: {'not installed' if isinstance(exc, FileNotFoundError) else 'not executable'} in the job sandbox image",
                "missing_executable": argv[0], "duration_ms": int((time.monotonic() - started) * 1000)}
    out, err = _CappedReader(LivePipe(child.stdout, "stdout", suite)), _CappedReader(LivePipe(child.stderr, "stderr", suite))
    out.start()
    err.start()
    timed_out = False
    try:
        child.wait(timeout=request["timeout"])
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(child.pid)
        child.wait(timeout=5)
    stdout, _ = out.join()
    stderr, _ = err.join()
    result = {"passed": child.returncode == 0 and not timed_out, "return_code": child.returncode,
              "timed_out": timed_out, "stdout": stdout, "stderr": stderr,
              "duration_ms": int((time.monotonic() - started) * 1000)}
    if junit:
        path = Path("/workspace") / junit
        # Any size: a big test suite writes a big report (the entity check in parse_junit is
        # the XML safety control, not the size).
        if (path.resolve().is_relative_to(Path("/workspace")) or path == Path(default_junit)) and path.is_file():
            from mu.jobs.checks import parse_junit
            try:
                text = path.read_text(errors="replace")
                result["junit"] = parse_junit(text)
                result["junit_xml"] = text
                if result["junit"]["failed"]:
                    result["passed"] = False
            except Exception as exc:
                result["passed"] = False
                result["stderr"] += f"\nInvalid JUnit report: {exc}"
        else:
            result["passed"] = False
            result["stderr"] += "\nConfigured JUnit report was not produced"
    return result


if __name__ == "__main__":
    main()
