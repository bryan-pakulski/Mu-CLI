# Mu-CLI GUI

Three-panel Operate UI:
- **Left**: session/runtime controls
- **Middle**: chat + job interaction
- **Right**: metadata/approvals inspector

## Run via Makefile

```bash
# Full app (API + GUI served at /gui)
make run-gui

# Static-only preview (no API backend)
make run-gui-static
```

By default, `make run-gui` serves the GUI at:
- `http://127.0.0.1:8000/gui`
