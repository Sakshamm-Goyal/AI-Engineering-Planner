import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .azure import AzureResponses
from .config import ROOT, Settings
from .errors import AppError
from .middleware import RequestGuards
from .models import Extraction, PlanRequest, PlanResult
from .service import PlannerService

logger = logging.getLogger("planner")


def create_app(settings: Settings | None = None, provider=None) -> FastAPI:
    settings = settings or Settings()
    llm = provider or AzureResponses(settings)
    service = PlannerService(settings, llm)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await llm.close()

    app = FastAPI(title="AI Engineering Planner", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(RequestGuards, max_bytes=settings.max_file_bytes + 2 * 1024 * 1024)
    app.state.active_runs = 0

    @asynccontextmanager
    async def run_slot():
        # No await between checking/incrementing: atomic within this event loop.
        if app.state.active_runs >= settings.max_concurrent_runs:
            raise AppError("busy", "The planner is processing its current runs. Retry when one finishes.", 503)
        app.state.active_runs += 1
        try:
            async with asyncio.timeout(settings.pipeline_timeout_seconds):
                yield
        except TimeoutError as exc:
            raise AppError("pipeline_timeout", "This run exceeded the total processing limit and was stopped. Split the document or adjust the configured limit.", 504) from exc
        finally:
            app.state.active_runs -= 1

    @app.exception_handler(AppError)
    async def application_error(request, exc: AppError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                  for e in exc.errors()][:10]
        return JSONResponse({"error": {"code": "invalid_request", "message": "The request contains invalid or missing fields.", "details": errors}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Log only the exception class; source-bearing bodies are not logged.
        logger.error("Unhandled application error: %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "internal_error", "message": "An unexpected error occurred. Retry or check the server configuration."}}, status_code=500)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "azure_configured": settings.configured,
                "deployment": settings.azure_openai_deployment,
                "max_file_mb": settings.max_file_mb, "max_pages": settings.max_pages,
                "max_vision_pages": settings.max_vision_pages,
                "pipeline_timeout_seconds": settings.pipeline_timeout_seconds,
                "note": "Configuration presence is not a live Azure connectivity check."}

    @app.post("/api/extract", response_model=Extraction)
    async def extract(
        file: Annotated[UploadFile, File()],
        consent: Annotated[bool, Form()],
        implementation_context: Annotated[str, Form(max_length=12000)] = "",
        vision_mode: Annotated[Literal["auto", "all"], Form()] = "auto",
    ):
        try:
            if not consent:
                raise AppError("consent_required", "Confirm that this document may be sent to the configured Azure deployment.")
            if not settings.configured:
                raise AppError("azure_not_configured", "Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT in .env, then restart. The sample works without credentials.", 503)
            name = (file.filename or "document.pdf").replace("\\", "/").split("/")[-1]
            name = "".join(c for c in name if c.isprintable())[:180] or "document.pdf"
            if not name.lower().endswith(".pdf") or file.content_type not in ("application/pdf", "application/octet-stream", None):
                raise AppError("unsupported_file", "Upload a PDF file, not a renamed document or image.", 415)
            async with run_slot():
                data = await file.read(settings.max_file_bytes + 1)
                if len(data) > settings.max_file_bytes:
                    raise AppError("file_limit", f"The PDF exceeds the {settings.max_file_mb} MB limit.", 413)
                if not data:
                    raise AppError("empty_file", "The uploaded PDF is empty.")
                return await service.extract(data, name, implementation_context, vision_mode)
        finally:
            await file.close()

    @app.post("/api/plan", response_model=PlanResult)
    async def plan(request: PlanRequest):
        async with run_slot():
            return await service.generate_plan(request)

    @app.get("/api/sample", response_model=PlanResult)
    async def sample():
        sample_path = ROOT / "examples/sample-output.json"
        if not sample_path.exists():
            raise AppError("sample_missing", "The bundled sample output is missing. Run python scripts/build_examples.py.", 503)
        return PlanResult.model_validate_json(sample_path.read_text(encoding="utf-8"))

    @app.get("/api/sample/pdf")
    async def sample_pdf():
        return FileResponse(ROOT / "examples/team-notes-prd.pdf", media_type="application/pdf", filename="team-notes-prd.pdf")

    # Static files are deliberately last; /api routes retain their own 404 behavior.
    @app.get("/")
    async def index():
        return FileResponse(ROOT / "frontend/index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "frontend/dist"), name="assets")
    return app


app = create_app()
