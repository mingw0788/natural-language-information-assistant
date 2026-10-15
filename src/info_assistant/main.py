from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_ROOT / "web" / "templates"))


def create_app(settings: Any | None = None) -> FastAPI:
    app = FastAPI(title="自然语言信息采集助手", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.mount(
        "/static",
        StaticFiles(directory=str(APP_ROOT / "web" / "static")),
        name="static",
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"request": request},
        )

    return app


app = create_app()
