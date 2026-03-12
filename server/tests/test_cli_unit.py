from cli.mu_cli import build_parser


def test_cli_parser_commands() -> None:
    parser = build_parser()
    args = parser.parse_args(["session-create", "/tmp/work"])
    assert args.command == "session-create"
    assert args.workspace == "/tmp/work"

    args = parser.parse_args(["job-start", "sess-1", "do thing", "--watch"])
    assert args.command == "job-start"
    assert args.watch is True

    args = parser.parse_args(["loop", "/tmp/work", "implement feature", "--tool", "shell.exec"])
    assert args.command == "loop"
    assert args.tool == "shell.exec"
