from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.app.api.routes import router
from server.app.persistence.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Mu-CLI Server", lifespan=lifespan)
    app.include_router(router)

    gui_dir = Path(__file__).resolve().parents[2] / "gui"
    app.mount("/gui/assets", StaticFiles(directory=str(gui_dir)), name="gui-assets")

    @app.get("/gui", include_in_schema=False)
    async def gui_index() -> FileResponse:
        return FileResponse(gui_dir / "index.html")

    return app


app = create_app()
