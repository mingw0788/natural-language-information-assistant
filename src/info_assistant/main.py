from __future__ import annotations

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from info_assistant.providers.base import ModelProvider, SearchProvider
from info_assistant.providers.brave_search import BraveSearchProvider
from info_assistant.providers.openai_compatible import OpenAICompatibleModelProvider
from info_assistant.services.browser_reader import BrowserReader
from info_assistant.services.budget_ledger import BudgetLedger
from info_assistant.services.capabilities import CapabilityChecker
from info_assistant.services.collection_runner import CollectionRunner
from info_assistant.services.credentials import CredentialStore, WindowsCredentialStore
from info_assistant.services.progress_events import EventReader
from info_assistant.services.provider_settings import ProviderSettingsStore
from info_assistant.services.recovery import RecoveryService
from info_assistant.services.retention_service import RetentionService
from info_assistant.services.run_control import RunControl
from info_assistant.services.source_fetcher import SourceFetcher
from info_assistant.services.task_planner import TaskPlanner
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository
from info_assistant.web.routes_history import router as history_router
from info_assistant.web.routes_settings import router as settings_router
from info_assistant.web.routes_tasks import router as tasks_router

APP_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_ROOT / "web" / "templates"))


@dataclass
class AppSettings:
    data_dir: Path | str | None = None
    credentials: CredentialStore | None = None
    search_provider: SearchProvider | None = None
    model_provider: ModelProvider | None = None
    fetcher: SourceFetcher | None = None
    browser_reader: Any | None = None
    browser_channel: str | None = None
    auto_dispatch: bool = True


def _default_data_dir() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "NaturalLanguageInformationAssistant"


def _hostname(host_header: str) -> str | None:
    try:
        return (urlsplit(f"//{host_header}").hostname or "").casefold() or None
    except ValueError:
        return None


def create_app(settings: AppSettings | None = None) -> FastAPI:
    config = settings or AppSettings()
    data_dir = Path(config.data_dir) if config.data_dir else _default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    if config.browser_channel not in {None, "chrome", "msedge"}:
        raise ValueError("browser_channel must be chrome, msedge, or None")
    database = Database(data_dir / "assistant.sqlite3")
    database.migrate()
    credentials = config.credentials or WindowsCredentialStore()
    provider_settings = ProviderSettingsStore(database, credentials)
    repository = Repository(database)
    ledger = BudgetLedger(database)
    control = RunControl(database, repository, ledger)
    recovery = RecoveryService(database, repository, control)
    event_reader = EventReader(repository)
    fetcher = config.fetcher or SourceFetcher()
    browser_reader = config.browser_reader or BrowserReader(
        fetcher,
        browser_channel=config.browser_channel,
    )
    capability_checker = CapabilityChecker(provider_settings)
    runner = CollectionRunner(
        database=database,
        repository=repository,
        control=control,
        ledger=ledger,
        fetcher=fetcher,
        search_provider=config.search_provider or BraveSearchProvider(credentials),
        model_provider=config.model_provider,
        browser_reader=browser_reader,
        capability_checker=capability_checker,
    )
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="collection-runner")
    csrf_token = secrets.token_urlsafe(32)
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    if not config.auto_dispatch or config.data_dir is not None:
        allowed_hosts.add("testserver")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        recovery.recover_after_restart()
        RetentionService(database).purge_expired()
        yield
        executor.shutdown(wait=False, cancel_futures=False)

    app = FastAPI(
        title="自然语言信息采集助手",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.data_dir = data_dir
    app.state.database = database
    app.state.credentials = credentials
    app.state.provider_settings = provider_settings
    app.state.repository = repository
    app.state.ledger = ledger
    app.state.control = control
    app.state.recovery = recovery
    app.state.event_reader = event_reader
    app.state.fetcher = fetcher
    app.state.browser_reader = browser_reader
    app.state.runner = runner
    app.state.capability_checker = capability_checker
    app.state.planner = TaskPlanner()
    app.state.executor = executor
    app.state.csrf_token = csrf_token
    app.state.auto_dispatch = config.auto_dispatch

    def prepare_model_provider() -> None:
        if config.model_provider is not None:
            runner.model_provider = config.model_provider
            return
        current = provider_settings.public_settings()
        if current.model_configured:
            runner.model_provider = OpenAICompatibleModelProvider(
                current.model_base_url,
                current.model_name,
                credentials,
            )
        else:
            runner.model_provider = None

    def dispatch_run(run_id: str) -> None:
        if not config.auto_dispatch:
            return

        def run_job() -> None:
            prepare_model_provider()
            runner.run(run_id)

        executor.submit(run_job)

    app.state.dispatch_run = dispatch_run
    app.mount(
        "/static",
        StaticFiles(directory=str(APP_ROOT / "web" / "static")),
        name="static",
    )
    app.include_router(tasks_router)
    app.include_router(settings_router)
    app.include_router(history_router)

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host_header = request.headers.get("host", "")
        host_name = _hostname(host_header)
        if host_name not in allowed_hosts:
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin", "")
            try:
                parsed_origin = urlsplit(origin)
            except ValueError:
                parsed_origin = urlsplit("")
            if (
                parsed_origin.scheme not in {"http", "https"}
                or parsed_origin.scheme != request.url.scheme
                or parsed_origin.netloc.casefold() != host_header.casefold()
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
                or request.headers.get("x-csrf-token") != csrf_token
            ):
                return JSONResponse(
                    {"detail": "本机来源校验失败，请刷新页面后重试"}, status_code=403
                )
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        providers = provider_settings.public_settings()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "request": request,
                "csrf_token": csrf_token,
                "providers": providers,
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "request": request,
                "csrf_token": csrf_token,
                "providers": provider_settings.public_settings(),
            },
        )

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="history.html",
            context={"request": request, "csrf_token": csrf_token},
        )

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_page(task_id: str, request: Request) -> HTMLResponse:
        try:
            task = repository.get_task(task_id)
        except KeyError:
            return templates.TemplateResponse(
                request=request,
                name="error.html",
                context={"request": request, "csrf_token": csrf_token, "message": "任务不存在"},
                status_code=404,
            )
        return templates.TemplateResponse(
            request=request,
            name="task.html",
            context={"request": request, "csrf_token": csrf_token, "task": task},
        )

    return app


_configured_browser_channel = os.environ.get("INFO_ASSISTANT_BROWSER_CHANNEL", "").casefold()
app = create_app(
    AppSettings(
        browser_channel=(
            _configured_browser_channel
            if _configured_browser_channel in {"chrome", "msedge"}
            else None
        )
    )
)
