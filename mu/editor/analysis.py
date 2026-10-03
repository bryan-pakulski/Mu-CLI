"""Select the code being discussed before retrieving surrounding definitions."""

from __future__ import annotations

import ast
import re
import json

from .context import Workspace, scrub
from .index import DEFINITION, identifiers, structure
from providers.base import Message, MessagePart
from utils.token_estimator import estimate_tokens


def answer_messages(question, snapshot, sources, history, token_limit=6000):
    """A bounded answer request made from evidence, not prior tool-call turns."""
    data = {
        "question": question,
        "focus": snapshot.get("focus"),
        "path": snapshot["path"],
        "conversation": [
            {"role": message.role, "text": part.text[:2000]}
            for message in history[-4:]
            for part in message.parts
            if part.type == "text" and part.text and part.text.strip()
        ],
        "evidence": [],
    }
    # The focus is already bounded. For oversized explicit selections leave room
    # for actual definitions and the question rather than copying it twice.
    if data["focus"]:
        data["focus"] = dict(data["focus"], text=data["focus"]["text"][:4000])
    while estimate_tokens(json.dumps(data, ensure_ascii=False)) > token_limit * 2 // 3:
        if data["conversation"]:
            data["conversation"].pop(0)
        elif data["focus"] and len(data["focus"]["text"]) > 100:
            data["focus"]["text"] = data["focus"]["text"][
                : len(data["focus"]["text"]) // 2
            ]
        elif len(data["question"]) > 100:
            data["question"] = data["question"][: len(data["question"]) // 2]
        else:
            break
    seen = set()
    ordered = sorted(reversed(sources), key=lambda source: "score" in source)
    for source in ordered:
        key = (source["path"], source["line"])
        if key in seen:
            continue
        seen.add(key)
        item = {
            "path": source["path"],
            "line": source["line"],
            "text": source["text"][:5000],
        }
        data["evidence"].append(item)
        while (
            estimate_tokens(json.dumps(data, ensure_ascii=False)) > token_limit
            and len(item["text"]) > 200
        ):
            item["text"] = item["text"][: len(item["text"]) // 2]
        if estimate_tokens(json.dumps(data, ensure_ascii=False)) > token_limit:
            data["evidence"].pop()
        if len(data["evidence"]) >= 12:
            break
    if estimate_tokens(json.dumps(data, ensure_ascii=False)) > token_limit:
        raise ValueError("Answer context cannot fit the configured token budget")
    return [
        Message(
            "user", [MessagePart("text", text=json.dumps(data, ensure_ascii=False))]
        )
    ]


COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
DECLARATION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def focused_code(params: dict, ws: Workspace, snapshot: dict):
    """Use a visual selection, small Python statement/block, or lexical fallback.

    All ranges address the current unsaved buffer. Large enclosing functions are
    signatures, not an invitation to explain every unrelated branch inside them.
    """
    lines = params["text"].split("\n")

    def span(start, end):
        return {
            "path": snapshot["path"],
            "line": start + 1,
            "end_line": end,
            "text": scrub("\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end)))[
                :12000
            ],
        }

    row = snapshot["row"]
    start, end = max(0, row - 3), min(len(lines), row + 9)
    tree = None
    if snapshot["filetype"] == "python" or snapshot["path"].endswith(".py"):
        try:
            tree = ast.parse(params["text"])
        except (SyntaxError, ValueError, RecursionError):
            pass
    selection = params.get("selection")
    if selection:
        start, end = selection.get("start"), selection.get("end")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or not 0 <= start < end <= len(lines)
        ):
            raise ValueError("Invalid selection range")
    elif tree:
        statements = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.stmt) and node.lineno <= row + 1 <= node.end_lineno
        ]
        small = [node for node in statements if node.end_lineno - node.lineno < 40]
        blocks = [node for node in small if isinstance(node, COMPOUND)]
        candidates = blocks or small
        if candidates:
            node = min(candidates, key=lambda n: n.end_lineno - n.lineno)
            start, end = node.lineno - 1, node.end_lineno
    # Clamp fallback at same-indent siblings rather than pulling in another mode.
    elif not selection:
        start = row
        indent = len(lines[row]) - len(lines[row].lstrip())
        for following in range(row + 1, end):
            line = lines[following]
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                end = following
                break
    end = min(end, start + 120)
    focus = span(start, end)
    focus["reason"] = "selected code" if selection else "code at cursor"
    focus["end_line"] = end
    raw = "\n".join(lines[start:end])
    symbols = identifiers(raw)
    imports = []
    local_sources = []
    # Parseable Python gives exact loaded names and import bindings. Comments and
    # strings are not retrieval queries (e.g. parser help text saying 'workspace').
    if tree:
        nodes = [
            node for node in ast.walk(tree) if start < getattr(node, "lineno", 0) <= end
        ]
        loaded = {
            node.id
            for node in nodes
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        # Resolve annotated receivers such as invoice: Invoice even when the
        # function signature sits outside the selected block.
        for parent in ast.walk(tree):
            if (
                isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                and parent.lineno <= start + 1 <= parent.end_lineno
            ):
                for argument in [
                    *parent.args.posonlyargs,
                    *parent.args.args,
                    *parent.args.kwonlyargs,
                ]:
                    if argument.arg in loaded and argument.annotation:
                        loaded.update(
                            node.id
                            for node in ast.walk(argument.annotation)
                            if isinstance(node, ast.Name)
                        )
        attributes = {node.attr for node in nodes if isinstance(node, ast.Attribute)}
        symbols = loaded | attributes
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bindings = {
                    alias.asname or alias.name.split(".")[0] for alias in node.names
                }
                if bindings & loaded:
                    imports.append("\n".join(lines[node.lineno - 1 : node.end_lineno]))
                    symbols.update(
                        alias.name.split(".")[-1]
                        for alias in node.names
                        if (alias.asname or alias.name.split(".")[0]) in loaded
                    )
            elif (
                isinstance(node, DECLARATION)
                and node.name in loaded
                and not start < node.lineno <= end
            ):
                source = span(node.lineno - 1, min(node.end_lineno, node.lineno + 59))
                source["reason"] = "referenced definition in current buffer"
                local_sources.append(source)
        # Named arguments and common receiver variables do not identify APIs.
        symbols -= {
            "args",
            "self",
            "cls",
            "parser",
            "main",
            "None",
            "True",
            "False",
            "error",
            "get",
            "set",
            "read",
            "write",
            "items",
            "keys",
            "values",
            "append",
            "pop",
            "exit",
        }
    else:
        all_imports, _ = structure(params["text"], row)
        imports = [
            line for line in all_imports.splitlines() if identifiers(line) & symbols
        ]
        for number, line in enumerate(lines):
            match = DEFINITION.match(line)
            if match and match.group(1) in symbols and not start <= number < end:
                source = span(number, min(len(lines), number + 24))
                source["reason"] = "referenced declaration"
                local_sources.append(source)
    snapshot["focus"] = focus
    snapshot["prefix"] = scrub("\n".join(lines[max(0, start - 3) : start]))[-800:]
    snapshot["suffix"] = ""  # The focus includes both sides of the cursor.
    snapshot["imports"] = scrub("\n".join(dict.fromkeys(imports)))[:1800]
    snapshot["buffers"] = []
    snapshot["recent_edits"] = []
    return " ".join(sorted(symbols)), [focus, *local_sources[:3]]


def cited_sources(answer, sources):
    """Keep citations distinct from available context/search hits for the UI."""
    cited = []
    seen = set()
    for source in sources:
        path = source["path"]
        for match in re.finditer(re.escape(path) + r":(\d+)", answer):
            line = int(match[1])
            end = source.get("end_line", source["line"] + source["text"].count("\n"))
            if source["line"] <= line <= end and (path, line) not in seen:
                cited.append(dict(source, line=line))
                seen.add((path, line))
    return cited
