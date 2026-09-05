import asyncio

import pytest

from planner.config import ROOT
from planner.errors import AppError
from planner.pdf import read_pdf
from planner.pdf_worker import inspect_pdf


@pytest.mark.parametrize("name,native,vision", [("team-notes-prd.pdf", 3, 0), ("team-notes-scanned.pdf", 0, 3), ("team-notes-mixed.pdf", 1, 2), ("table-and-ambiguity.pdf", 1, 0)])
async def test_page_routing_uses_real_pdf_fixtures(settings, name, native, vision):
    pages = await read_pdf((ROOT / "examples" / name).read_bytes(), settings, "auto")
    assert sum(p["image"] is None for p in pages) == native
    assert sum(p["image"] is not None for p in pages) == vision
    assert [p["number"] for p in pages] == list(range(1, native + vision + 1))
    for page in pages:
        if page["image"]:
            assert page["image"].startswith("iVBOR")  # PNG data, not an image URL to fetch.


async def test_reading_mode_does_not_force_visual_rendering(settings):
    pages = await read_pdf((ROOT / "examples/team-notes-prd.pdf").read_bytes(), settings, "all")
    assert all(p["image"] is None for p in pages)


@pytest.mark.parametrize("name,code", [("encrypted.pdf", "encrypted_pdf"), ("malformed.pdf", "invalid_pdf")])
async def test_invalid_pdf_files_fail_explicitly(settings, name, code):
    with pytest.raises(AppError) as error:
        await read_pdf((ROOT / "examples" / name).read_bytes(), settings, "auto")
    assert error.value.code == code


async def test_renamed_text_is_not_pdf(settings):
    with pytest.raises(AppError) as error:
        await read_pdf(b"Not a PDF at all", settings, "auto")
    assert error.value.code == "invalid_pdf"


async def test_pdf_page_limit(settings):
    settings.max_pages = 2
    with pytest.raises(AppError) as error:
        await read_pdf((ROOT / "examples/team-notes-prd.pdf").read_bytes(), settings, "auto")
    assert error.value.code == "page_limit"


async def test_vision_budget_does_not_silently_skip_pages(settings):
    settings.max_vision_pages = 1
    with pytest.raises(AppError) as error:
        await read_pdf((ROOT / "examples/team-notes-scanned.pdf").read_bytes(), settings, "auto")
    assert error.value.code == "vision_page_limit"


async def test_text_budget_does_not_truncate(settings):
    settings.max_document_chars = 1000
    with pytest.raises(AppError) as error:
        await read_pdf((ROOT / "examples/team-notes-prd.pdf").read_bytes(), settings, "auto")
    assert error.value.code == "document_limit"


async def test_parser_timeout_kills_and_reaps_process(settings, monkeypatch):
    class SlowProcess:
        returncode = None
        killed = False
        waited = False
        async def communicate(self, data):
            await asyncio.sleep(20)
        def kill(self):
            self.killed = True
        async def wait(self):
            self.waited = True
    process = SlowProcess()
    async def create(*args, **kwargs): return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    settings.parser_timeout_seconds = 0.01
    with pytest.raises(AppError) as error:
        await read_pdf(b"%PDF-fake", settings, "auto")
    assert process.killed and process.waited and error.value.code == "parser_timeout"


async def test_cancellation_also_cleans_up_child(settings, monkeypatch):
    class CancelProcess:
        returncode = None
        killed = False
        waited = False
        async def communicate(self, data): raise asyncio.CancelledError()
        def kill(self): self.killed = True
        async def wait(self): self.waited = True
    process = CancelProcess()
    async def create(*args, **kwargs): return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(asyncio.CancelledError):
        await read_pdf(b"%PDF-fake", settings, "auto")
    assert process.killed and process.waited


def test_worker_does_not_follow_links_or_execute_pdf_content():
    limits = {"max_pages": 30, "max_chars": 100000, "max_vision_pages": 12, "vision_mode": "auto"}
    result = inspect_pdf((ROOT / "examples/table-and-ambiguity.pdf").read_bytes(), limits)
    assert "pages" in result
    # This proves adversarial text remains data; it does NOT prove an LLM is injection-proof.
    assert "Ignore earlier instructions" in result["pages"][0]["text"]
