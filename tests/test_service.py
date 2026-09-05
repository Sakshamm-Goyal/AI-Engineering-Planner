import pytest

from planner.config import ROOT
from planner.errors import AppError
from planner.models import PlanDraft, PlanRequest, Review, Usage
from planner.service import PlannerService


async def test_native_pipeline_uses_two_model_stages(settings, provider):
    service = PlannerService(settings, provider)
    extraction = await service.extract((ROOT / "examples/team-notes-prd.pdf").read_bytes(), "prd.pdf", "Use FastAPI", "auto")
    result = await service.generate_plan(PlanRequest(extraction=extraction, review=Review(acknowledge_warnings=True, implementation_context="Use FastAPI")))
    assert len(provider.calls) == 2
    assert len(result.plan.tasks) == 9 and result.usage.calls == 2
    assert result.markdown.startswith("# Team Notes")
    assert "STOP" in result.markdown


async def test_scanned_pipeline_uses_local_ocr_provenance(settings, provider):
    service = PlannerService(settings, provider)
    extraction = await service.extract((ROOT / "examples/team-notes-scanned.pdf").read_bytes(), "scan.pdf", "", "auto")
    assert len(provider.calls) == 1  # Requirements inventory only; OCR stays local.
    assert all(p.method == "ocr" for p in extraction.pages)
    assert all(p.native_text == "" for p in extraction.pages)
    assert "Locally OCR-transcribed" in extraction.pages[0].warnings[-1]


async def test_unreadable_scan_cannot_produce_partial_inventory(settings, provider):
    service = PlannerService(settings, provider)
    service.local_ocr = lambda image: ""
    with pytest.raises(AppError) as error:
        await service.extract((ROOT / "examples/team-notes-scanned.pdf").read_bytes(), "scan.pdf", "", "auto")
    assert error.value.code == "unreadable_page" and not provider.calls


async def test_genuinely_blank_document_rejected(settings, provider):
    with pytest.raises(AppError) as error:
        await PlannerService(settings, provider).extract((ROOT / "examples/blank.pdf").read_bytes(), "blank.pdf", "", "auto")
    assert error.value.code == "unreadable_page"


async def test_invalid_plan_is_repaired_once(settings, provider, draft, extraction):
    count = 0
    requests = []
    async def generate(model, instruction, payload, image=None):
        nonlocal count
        count += 1
        requests.append(payload)
        raw = draft.model_dump()
        if count == 1:
            raw["tasks"][0]["dependencies"] = ["T-002"]  # cycle
        return raw, Usage(calls=1)
    provider.generate = generate
    result = await PlannerService(settings, provider).generate_plan(PlanRequest(extraction=extraction, review=Review(acknowledge_warnings=True)))
    assert count == 2 and result.usage.calls == 2
    assert "validation_errors" in requests[1]
    assert "cycle" in " ".join(requests[1]["validation_errors"])


async def test_repair_is_bounded_not_an_agent_loop(settings, provider, extraction):
    count = 0
    async def invalid(*args, **kwargs):
        nonlocal count
        count += 1
        return {"junk": "sensitive source contents"}, Usage(calls=1)
    provider.generate = invalid
    with pytest.raises(AppError) as error:
        await PlannerService(settings, provider).generate_plan(PlanRequest(extraction=extraction, review=Review(acknowledge_warnings=True)))
    assert count == 2 and error.value.code == "generation_invalid"
    assert "sensitive source contents" not in error.value.message


async def test_sample_is_never_sent_to_provider(settings, provider, extraction):
    extraction.mode = "sample"
    with pytest.raises(AppError) as error:
        await PlannerService(settings, provider).generate_plan(PlanRequest(extraction=extraction, review=Review()))
    assert error.value.code == "sample_is_read_only" and not provider.calls


async def test_planning_receives_full_source_and_labelled_user_correction(settings, provider, extraction):
    from planner.models import Correction
    review = Review(acknowledge_warnings=True, corrections=[Correction(requirement_id="R-001", description="Use my explicitly revised title limit.")])
    await PlannerService(settings, provider).generate_plan(PlanRequest(extraction=extraction, review=review))
    payload = provider.calls[0][2]
    assert len(payload["source_evidence"]) == len(extraction.document.requirements)
    assert payload["reviewed_requirements"][0]["description_source"] == "user_correction"
    assert payload["reviewed_requirements"][0]["original_description"] == extraction.document.requirements[0].description


async def test_invalid_source_page_sequence_rejected(settings, provider, extraction):
    extraction.pages[1].number = 1
    with pytest.raises(AppError) as error:
        await PlannerService(settings, provider).generate_plan(PlanRequest(extraction=extraction, review=Review(acknowledge_warnings=True)))
    assert error.value.code == "invalid_pages"
