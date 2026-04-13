#!/usr/bin/env python3
"""Launches Mu-CLI server mode plus static GUI hosting with rolling logs."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from logging import Formatter, StreamHandler, getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path


def build_logger(log_path: Path, name: str):
    logger = getLogger(name)
    logger.setLevel("INFO")
    logger.handlers.clear()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fmt = Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stdout_handler = StreamHandler()
    stdout_handler.setFormatter(fmt)
    logger.addHandler(stdout_handler)

    return logger


def pump_stream(stream, logger, prefix: str):
    for line in iter(stream.readline, ""):
        text = line.rstrip("\n")
        if text:
            logger.info("[%s] %s", prefix, text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Mu-CLI server + GUI")
    parser.add_argument("--server-host", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=8765)
    parser.add_argument("--gui-host", default="127.0.0.1")
    parser.add_argument("--gui-port", type=int, default=4173)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--yolo", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    gui_dir = root / "gui"
    log_dir = Path.home() / ".mucli" / "gui" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = build_logger(log_dir / "launcher.log", "mucli.gui.launcher")

    server_cmd = [
        sys.executable,
        str(root / "mucli.py"),
        "--server",
        "--host",
        args.server_host,
        "--port",
        str(args.server_port),
    ]
    if args.provider:
        server_cmd.extend(["--provider", args.provider])
    if args.model:
        server_cmd.extend(["--model", args.model])
    if args.yolo:
        server_cmd.append("--yolo")

    gui_cmd = [
        sys.executable,
        str(gui_dir / "proxy_server.py"),
        "--host",
        args.gui_host,
        "--port",
        str(args.gui_port),
        "--api-host",
        args.server_host,
        "--api-port",
        str(args.server_port),
    ]

    logger.info("Starting server: %s", " ".join(server_cmd))
    server_proc = subprocess.Popen(
        server_cmd,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    logger.info("Starting GUI static host: %s", " ".join(gui_cmd))
    gui_proc = subprocess.Popen(
        gui_cmd,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    threads = [
        threading.Thread(
            target=pump_stream,
            args=(server_proc.stdout, logger, "server"),
            daemon=True,
        ),
        threading.Thread(
            target=pump_stream,
            args=(gui_proc.stdout, logger, "gui"),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    logger.info("GUI available at http://%s:%s", args.gui_host, args.gui_port)
    logger.info("Mu-CLI API at http://%s:%s", args.server_host, args.server_port)

    stop_event = threading.Event()

    def shutdown(signum=None, frame=None):
        if stop_event.is_set():
            return
        stop_event.set()
        logger.info("Shutting down launcher...")
        for proc in (server_proc, gui_proc):
            if proc.poll() is None:
                proc.terminate()
        for proc in (server_proc, gui_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while not stop_event.is_set():
        if server_proc.poll() is not None:
            logger.error("Mu-CLI server exited with code %s", server_proc.returncode)
            shutdown()
            break
        if gui_proc.poll() is not None:
            logger.error("GUI host exited with code %s", gui_proc.returncode)
            shutdown()
            break
        stop_event.wait(0.4)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
