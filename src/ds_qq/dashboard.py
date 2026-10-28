import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from ds_qq.config import Settings, get_settings
from ds_qq.db import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_path)
        app.state.database = database
        try:
            yield
        finally:
            database.close()

    app = FastAPI(title="ds_qq usage dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/overview")
    async def overview(days: int = Query(default=30, ge=7, le=365)) -> dict:
        return app.state.database.usage_overview(days)

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="dashboard")
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    if settings.dashboard_host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("The usage dashboard can only bind to a loopback address")
    uvicorn.run(
        create_app(settings),
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
