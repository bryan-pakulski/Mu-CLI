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

    def __init__(self, pipe, stream):
        self.pipe, self.stream, self.sent = pipe, stream, 0

    def readline(self):
        chunk = self.pipe.readline(4096)
        if chunk and self.sent < 512 * 1024:
            with self.lock:
                print(json.dumps({"event": "check_output", "stream": self.stream, "text": chunk}), flush=True)
            self.sent += len(chunk)
        return chunk

    def close(self):
        self.pipe.close()


def main():
    from .egress import start_container_proxy
    from mu.jobs.verification import _CappedReader, _kill_process_group
    start_container_proxy()
    request = json.loads(Path("/artifacts/request.json").read_text())
    started = time.monotonic()
    argv = shlex.split(request["command"])
    junit = request.get("junit")
    if not junit and (Path(argv[0]).name == "pytest" or argv[:3] in (["python", "-m", "pytest"], ["python3", "-m", "pytest"])):
        junit = "/output/junit.xml"
        argv.append("--junitxml=" + junit)
    child = subprocess.Popen(argv, cwd="/workspace", shell=False,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    out, err = _CappedReader(LivePipe(child.stdout, "stdout")), _CappedReader(LivePipe(child.stderr, "stderr"))
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
        if (path.resolve().is_relative_to(Path("/workspace")) or path == Path("/output/junit.xml")) and path.is_file() and path.stat().st_size <= 8 * 1024 * 1024:
            from mu.jobs.checks import parse_junit
            try:
                result["junit"] = parse_junit(path.read_text())
                result["junit_xml"] = path.read_text()
                if result["junit"]["failed"]:
                    result["passed"] = False
            except Exception as exc:
                result["passed"] = False
                result["stderr"] += f"\nInvalid JUnit report: {exc}"
        else:
            result["passed"] = False
            result["stderr"] += "\nConfigured JUnit report was not produced"
    Path("/output/result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
