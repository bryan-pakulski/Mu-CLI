from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "agents" / "mu_cli" / "static"
MODULES = [
    STATIC / "app" / "store.js",
    STATIC / "app" / "network.js",
    STATIC / "app" / "render" / "core.js",
    STATIC / "app" / "main.js",
    STATIC / "app" / "events.js",
]
OUT = STATIC / "app.js"


def build() -> None:
    missing = [str(p) for p in MODULES if not p.exists()]
    if missing:
        raise SystemExit("Missing frontend modules:\n" + "\n".join(missing))

    parts = [
        "(() => {",
        "'use strict';",
        "",
    ]
    for module in MODULES:
        body = module.read_text(encoding="utf-8").rstrip() + "\n"
        parts.append(f"// >>> {module.relative_to(STATIC)}")
        parts.append(body)
    parts.append("})();")
    parts.append("")
    OUT.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    build()
