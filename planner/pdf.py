import asyncio
import json
import sys

from .config import ROOT, Settings
from .errors import AppError


async def read_pdf(data: bytes, settings: Settings, vision_mode: str) -> list[dict]:
    limits = {"max_pages": settings.max_pages, "max_chars": settings.max_document_chars,
              "max_vision_pages": settings.max_vision_pages, "vision_mode": vision_mode,
              "max_bytes": settings.max_file_bytes}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "planner.pdf_worker", json.dumps(limits), cwd=ROOT,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(data), settings.parser_timeout_seconds)
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise AppError("parser_timeout", "PDF parsing exceeded its time limit and was stopped. Split or re-export the document.", 422) from exc
    if process.returncode != 0:
        raise AppError("parser_failed", "The isolated PDF parser stopped unexpectedly. The document may exceed safe resource limits.")
    if len(stdout) > 48 * 1024 * 1024:
        raise AppError("parser_output_limit", "The PDF produced too much rendered content. Split the document.")
    try:
        result = json.loads(stdout)
    except ValueError as exc:
        raise AppError("parser_failed", "The PDF parser returned an invalid result.") from exc
    if "error" in result:
        raise AppError(result["error"], result["message"])
    return result["pages"]
