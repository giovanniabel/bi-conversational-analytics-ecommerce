"""
FastAPI web app: Claude chat alongside embedded Metabase dashboards.

The second UI in this project. Where the Streamlit app renders its own
Plotly charts, this one treats Metabase as the dashboarding layer: Claude
answers questions by querying through Metabase, and can persist an answer
as a saved question pinned to a dashboard that everyone sees.

Metabase provisioning runs lazily on first use rather than at import time,
so the container starts (and reports healthy) even while Metabase is still
booting.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.clickhouse_client import ClickHouseClient
from src.config import llm_config, metabase_config
from src.logger import get_logger, setup_logging
from src.metabase_agent import MetabaseAgent
from src.metabase_client import MetabaseClient, MetabaseError
from src.metabase_embed import EmbedSigner
from src.metabase_provision import provision
from src.schema_inspector import SchemaInspector
from src.sql_validator import SQLValidator

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class Backend:
    """
    Lazily-initialized Metabase wiring, shared across requests.

    Initialization touches the network (Metabase may still be booting), so
    it happens on first use behind a lock rather than at startup, and a
    failure leaves the app up and reporting *why* it isn't ready.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._error = ""
        self.client: Optional[MetabaseClient] = None
        self.agent: Optional[MetabaseAgent] = None
        self.signer: Optional[EmbedSigner] = None
        self.database_id: Optional[int] = None
        self.dashboard_id: Optional[int] = None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> str:
        return self._error

    def ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                client = MetabaseClient(
                    internal_url=metabase_config.internal_url,
                    username=metabase_config.admin_email,
                    password=metabase_config.admin_password,
                )
                result = provision(client)

                ch_client = ClickHouseClient()
                schema_inspector = SchemaInspector(ch_client)
                schema = schema_inspector.get_schema()
                validator = SQLValidator(
                    known_tables=set(schema.tables.keys()),
                    database=schema.database,
                )

                self.client = client
                self.database_id = result.database_id
                self.dashboard_id = result.dashboard_id
                self.signer = EmbedSigner(
                    public_url=metabase_config.public_url,
                    secret=result.embedding_secret,
                )
                self.agent = MetabaseAgent(
                    client=client,
                    database_id=result.database_id,
                    default_dashboard_id=result.dashboard_id,
                    schema_inspector=schema_inspector,
                    validator=validator,
                )
                self._ready = True
                self._error = ""
                logger.info("backend_ready", database_id=self.database_id,
                            dashboard_id=self.dashboard_id)
            except Exception as e:
                self._error = str(e)
                logger.error("backend_init_failed", error=str(e))
                raise


backend = Backend()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("webapp_starting", metabase=metabase_config.internal_url)

    # Warm up in the background so the first request isn't stuck behind
    # Metabase's boot, but never block startup on it.
    def warm() -> None:
        try:
            backend.ensure_ready()
        except Exception:
            logger.info("backend_warmup_deferred")

    threading.Thread(target=warm, daemon=True).start()
    yield


app = FastAPI(title="E-commerce Analytics — Metabase + Claude", lifespan=lifespan)


# ── models ────────────────────────────────────────────────────────────────

class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list)


class ActionOut(BaseModel):
    kind: str
    id: int
    name: str
    url: str = ""


class ChatResponse(BaseModel):
    answer: str
    actions: list[ActionOut] = Field(default_factory=list)
    queries_run: list[str] = Field(default_factory=list)
    dashboard_changed: bool = False
    error: str = ""


# ── api ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    """Liveness — intentionally does not require Metabase to be up."""
    return {"status": "ok"}


@app.get("/api/status")
def status() -> dict:
    """Readiness detail for the UI's connection strip."""
    llm_ok = bool(llm_config.api_key and llm_config.api_key != "your-api-key-here")

    if not backend.ready:
        try:
            backend.ensure_ready()
        except Exception:
            pass

    return {
        "ready": backend.ready,
        "error": backend.error,
        "llm_configured": llm_ok,
        "llm_provider": llm_config.provider,
        "llm_model": llm_config.model,
        "metabase_url": metabase_config.public_url,
        "embedding_enabled": bool(backend.signer and backend.signer.available),
        "dashboard_id": backend.dashboard_id,
        "database_id": backend.database_id,
    }


@app.get("/api/dashboards")
def dashboards() -> dict:
    backend.ensure_ready()
    assert backend.client is not None
    try:
        items = backend.client.list_dashboards()
    except MetabaseError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "dashboards": [
            {"id": d.id, "name": d.name, "description": d.description} for d in items
        ],
        "default_id": backend.dashboard_id,
    }


@app.get("/api/embed/dashboard/{dashboard_id}")
def embed_dashboard(dashboard_id: int) -> dict:
    """
    Hand the browser a signed iframe URL.

    Falls back to a plain Metabase link when no embedding secret is set, so
    the UI degrades to "open in Metabase" instead of showing a dead frame.
    """
    backend.ensure_ready()
    assert backend.client is not None and backend.signer is not None

    try:
        backend.client.enable_dashboard_embedding(dashboard_id)
    except MetabaseError as e:
        logger.info("embed_enable_skipped", dashboard=dashboard_id, reason=str(e)[:120])

    url = backend.signer.dashboard_url(dashboard_id)
    return {
        "embed_url": url,
        "fallback_url": f"{metabase_config.public_url}/dashboard/{dashboard_id}",
        "embedded": url is not None,
    }


@app.get("/api/embed/card/{card_id}")
def embed_card(card_id: int) -> dict:
    backend.ensure_ready()
    assert backend.client is not None and backend.signer is not None

    try:
        backend.client.enable_card_embedding(card_id)
    except MetabaseError as e:
        logger.info("embed_card_enable_skipped", card=card_id, reason=str(e)[:120])

    url = backend.signer.card_url(card_id)
    return {
        "embed_url": url,
        "fallback_url": f"{metabase_config.public_url}/question/{card_id}",
        "embedded": url is not None,
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    if not llm_config.api_key or llm_config.api_key == "your-api-key-here":
        raise HTTPException(
            status_code=503,
            detail="LLM_API_KEY is not configured. Set it in ai-analytics/.env.",
        )

    try:
        backend.ensure_ready()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Metabase is not ready yet: {e}") from e

    assert backend.agent is not None
    reply = backend.agent.chat(
        message=request.message,
        history=[turn.model_dump() for turn in request.history],
    )

    actions = [
        ActionOut(kind=a.kind, id=a.id, name=a.name, url=a.url) for a in reply.actions
    ]
    # Anything that changes what a dashboard shows should refresh the iframe.
    dashboard_changed = any(a.kind in {"pinned_card", "created_dashboard"} for a in reply.actions)

    return ChatResponse(
        answer=reply.answer,
        actions=actions,
        queries_run=reply.queries_run,
        dashboard_changed=dashboard_changed,
        error=reply.error,
    )


# ── static frontend ───────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _asset_version() -> str:
    """Newest static-asset mtime, used to bust caches after a redeploy."""
    try:
        return str(int(max(p.stat().st_mtime for p in STATIC_DIR.glob("*.*"))))
    except ValueError:
        return "0"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # The shell is always revalidated so a redeploy's new asset version is
    # picked up; the versioned CSS/JS behind it stay cacheable.
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__ASSET_VERSION__", _asset_version())
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
