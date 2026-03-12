# Mu-CLI Server

Python FastAPI scaffold implementing:
- Session and job persistence
- Job lifecycle transitions
- WebSocket streaming events
- Provider abstraction with Ollama adapter

Run locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn server.app.main:app --reload
```
