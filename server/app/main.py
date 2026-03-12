from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.app.api.routes import router
from server.app.persistence.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Mu-CLI Server", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
