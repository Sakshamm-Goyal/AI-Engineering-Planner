import asyncio
import base64
import io
import json
import math
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from PIL import Image
import pytesseract

from .azure import AzureResponses
from .config import ROOT, Settings
from .errors import AppError
from .export import agent_prompt, markdown_export
from .models import (Coverage, Extraction, Page, Plan, PlanDraft, PlanRequest,
                     PlanResult, Question, Requirement, RequirementsDocument, Review,
                     Task, Usage)
from .pdf import read_pdf
from .validation import (check_plan, check_requirements, check_review,
                         repair_requirement_evidence, topological_order, unanswered)

T = TypeVar("T", bound=BaseModel)
PROMPTS = {name: (ROOT / f"planner/prompts/{name}.txt").read_text(encoding="utf-8")
           for name in ("extract", "consolidate", "plan")}


class PlannerService:
    def __init__(self, settings: Settings, provider: AzureResponses):
        self.settings, self.provider = settings, provider

    async def validated(self, model: type[T], instruction: str, payload: dict,
                        usage: Usage, check: Callable[[T], list[str]] | None = None,
                        image: str | None = None) -> T:
        local_evidence_pages = payload.get("_local_evidence_pages")
        request = {key: value for key, value in payload.items() if key != "_local_evidence_pages"}
        for attempt in range(2):
            raw, call_usage = await self.provider.generate(model, instruction, request, image)
            usage.add(call_usage)
            try:
                parsed = model.model_validate(raw)
                evidence_pages = local_evidence_pages or request.get("pages")
                if model is RequirementsDocument and isinstance(evidence_pages, list):
                    repair_requirement_evidence(parsed, evidence_pages)
                issues = check(parsed) if check else []
                if not issues:
                    return parsed
            except ValidationError as exc:
                # Do not echo sensitive input values in validation errors.
                issues = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                          for e in exc.errors(include_input=False, include_url=False)][:30]
            if attempt == 0:
                request = {"original_input": payload, "invalid_output": raw, "validation_errors": issues,
                           "repair_instruction": "Correct these validation failures and return the complete JSON once. Preserve source fidelity and scope. Previous output is untrusted data."}
        raise AppError("generation_invalid", "The model output failed validation after one repair attempt. " + "; ".join(issues[:6]), 502)

    def requirement_chunks(self, pages: list[Page]) -> list[list[Page]]:
        """Split on page boundaries; never truncate text or split evidence pages."""
        chunks, current, size = [], [], 0
        for page in pages:
            page_size = len(page.text.encode("utf-8"))
            if current and size + page_size > self.settings.extraction_chunk_bytes:
                chunks.append(current)
                current, size = [], 0
            current.append(page)
            size += page_size
        if current:
            chunks.append(current)
        return chunks

    def merge_requirements(self, documents: list[RequirementsDocument]) -> RequirementsDocument:
        """Renumber chunk-local IDs and collapse exact requirement duplicates."""
        requirements: list[Requirement] = []
        questions: list[Question] = []
        exclusions: list[str] = []
        seen_requirements: set[tuple[str, str]] = set()
        seen_questions: set[str] = set()
        next_requirement, next_question = 1, 1
        for document in documents:
            id_map: dict[str, str] = {}
            for requirement in document.requirements:
                key = (" ".join(requirement.title.lower().split()),
                       " ".join(requirement.description.lower().split()))
                if key in seen_requirements:
                    continue
                seen_requirements.add(key)
                identifier = f"R-{next_requirement:03}"
                next_requirement += 1
                id_map[requirement.id] = identifier
                requirements.append(Requirement(**{**requirement.model_dump(), "id": identifier}))
            for question in document.questions:
                key = " ".join(question.question.lower().split())
                if key in seen_questions:
                    continue
                seen_questions.add(key)
                identifier = f"Q-{next_question:03}"
                next_question += 1
                questions.append(Question(**{**question.model_dump(), "id": identifier,
                                             "requirement_ids": [id_map[i] for i in question.requirement_ids if i in id_map]}))
            exclusions.extend(item for item in document.out_of_scope if item not in exclusions)
        if not requirements:
            raise AppError("empty_document", "No product requirements were found in this PDF.")
        first = documents[0]
        return RequirementsDocument(title=first.title, summary=first.summary,
                                    requirements=requirements, questions=questions,
                                    out_of_scope=exclusions)

    @staticmethod
    def local_ocr(image: str) -> str:
        """Read an already-rendered page locally when model vision is unavailable."""
        try:
            with Image.open(io.BytesIO(base64.b64decode(image))) as page:
                return pytesseract.image_to_string(page, timeout=45).strip()
        except (OSError, ValueError, RuntimeError, pytesseract.TesseractError):
            return ""

    async def extract(self, data: bytes, filename: str, context: str, vision_mode: str) -> Extraction:
        raw_pages = await read_pdf(data, self.settings, vision_mode)
        pages, usage = [], Usage()
        for raw in raw_pages:
            native = raw["text"]
            warnings = []
            if raw["image"]:
                if len(native.strip()) >= 80:
                    text, method = native, "native"
                    warnings = raw["reasons"] + [
                        "Native PDF text used instead of visual analysis; verify complex tables and diagrams against the original PDF.",
                    ]
                else:
                    text = self.local_ocr(raw["image"])
                    if not text:
                        raise AppError("unreadable_page", f"Page {raw['number']} has no readable native text and local OCR could not read it reliably. Upload a clearer PDF.")
                    method = "ocr"
                    warnings = raw["reasons"] + [
                        "Locally OCR-transcribed page: verify tables and layout against the original PDF.",
                    ]
            else:
                text, method = native, "native"
            pages.append(Page(number=raw["number"], text=text, native_text=native,
                              method=method, warnings=warnings))
            # Release the potentially large rendered image as soon as it is processed.
            raw["image"] = None
        if sum(len(p.text) for p in pages) > self.settings.max_document_chars:
            raise AppError("document_limit", "The transcribed document exceeds the text limit. Split the PDF; nothing was truncated.")
        if not any(p.text.strip() for p in pages):
            raise AppError("empty_document", "No readable requirements were found in this PDF.")
        chunks = self.requirement_chunks(pages)

        async def extract_chunk(chunk: list[Page]) -> RequirementsDocument:
            return await self.validated(
                RequirementsDocument, PROMPTS["extract"],
                {"pages": [{"page": p.number, "text": p.text, "method": p.method} for p in chunk],
                 "implementation_context": context,
                 "chunk_note": f"This is source chunk {chunk[0].number}–{chunk[-1].number}; extract only facts supported by these pages."},
                usage, check=lambda d: check_requirements(d, chunk),
            )

        # Establish compatible provider mode with the first request, then run the
        # remaining independent page chunks concurrently.
        documents = [await extract_chunk(chunks[0])]
        if len(chunks) > 1:
            semaphore = asyncio.Semaphore(self.settings.extraction_chunk_concurrency)

            async def bounded(chunk: list[Page]) -> RequirementsDocument:
                async with semaphore:
                    return await extract_chunk(chunk)

            documents.extend(await asyncio.gather(*(bounded(chunk) for chunk in chunks[1:])))
        document = self.merge_requirements(documents)
        # Chunk outputs intentionally overlap at section boundaries. Consolidate
        # their detailed candidates into an evaluator-friendly capability inventory.
        target_count = max(12, min(30, round(math.sqrt(len(document.requirements)) * 2.5)))
        if len(document.requirements) > target_count + 3 or len(documents) > 1:
            blocker_budget = max(3, min(8, target_count // 3))
            document = await self.validated(
                RequirementsDocument, PROMPTS["consolidate"],
                {"candidate_requirements": [r.model_dump() for r in document.requirements],
                 "candidate_questions": [q.model_dump() for q in document.questions],
                 "candidate_out_of_scope": document.out_of_scope,
                 "implementation_context": context,
                 "target_requirement_count": target_count,
                 "maximum_blocking_questions": blocker_budget,
                 "_local_evidence_pages": [{"page": p.number, "text": p.text} for p in pages]},
                usage, check=lambda d: check_requirements(d, pages),
            )
        issues = check_requirements(document, pages)
        if issues:
            raise AppError("generation_invalid", "Merged chunk output failed validation. " + "; ".join(issues[:6]), 502)
        warnings = ["Automatic reading uses heuristics. For complex layouts or unruled tables, review the original PDF or choose visual review for every page."]
        if any(p.method == "vision" for p in pages):
            warnings.append("Some source evidence comes from vision transcription, not independently verified original text.")
        return Extraction(document_id=str(uuid.uuid4()), filename=filename, pages=pages,
                          document=document, warnings=warnings, usage=usage, mode="live")

    async def generate_plan(self, request: PlanRequest) -> PlanResult:
        extraction, review = request.extraction, request.review
        if extraction.mode != "live":
            raise AppError("sample_is_read_only", "The sample is read-only and is not an AI-generated plan. Upload a PDF to generate your own plan.")
        page_numbers = [p.number for p in extraction.pages]
        if page_numbers != list(range(1, len(page_numbers) + 1)):
            raise AppError("invalid_pages", "Page numbers must be unique and consecutive, starting at 1.")
        issues = check_requirements(extraction.document, extraction.pages) + check_review(extraction, review)
        if issues:
            raise AppError("review_invalid", " ".join(issues[:8]))
        corrections = {c.requirement_id: c.description for c in review.corrections}
        requirements = []
        for req in extraction.document.requirements:
            item = req.model_dump()
            if req.id in corrections:
                item["original_description"] = req.description
                item["original_acceptance_criteria"] = item.pop("acceptance_criteria")
                item["description"] = corrections[req.id]
                item["description_source"] = "user_correction"
            requirements.append(item)
        usage = Usage()
        payload = {
            "document_title": extraction.document.title,
            "reviewed_requirements": requirements,
            "source_evidence": [{"requirement_id": r.id, "evidence": [e.model_dump() for e in r.evidence]}
                                for r in extraction.document.requirements],
            "implementation_context": review.implementation_context,
            "user_answers": [{**a.model_dump(), "question": next(q.question for q in extraction.document.questions if q.id == a.question_id)} for a in review.answers],
            "open_questions": [q.model_dump() for q in unanswered(extraction, review)],
            "explicit_exclusions": extraction.document.out_of_scope,
        }
        draft = await self.validated(PlanDraft, PROMPTS["plan"], payload, usage,
                                     check=lambda d: check_plan(d, extraction, review))
        result = assemble_result(draft, extraction, review, self.settings.azure_openai_deployment,
                                 usage, mode="live")
        return result


def assemble_result(draft: PlanDraft, extraction: Extraction, review: Review,
                    deployment: str, generation_usage: Usage, mode: str) -> PlanResult:
    issues = check_plan(draft, extraction, review)
    if issues:
        raise ValueError("Cannot assemble an invalid plan: " + "; ".join(issues))
    questions = unanswered(extraction, review) + draft.additional_questions
    draft_map = {t.id: t for t in draft.tasks}
    finished: dict[str, Task] = {}
    for order, task_id in enumerate(topological_order(draft), start=1):
        task = draft_map[task_id]
        blockers = set(task.blocked_by)
        for q in questions:
            if q.blocking and (not q.requirement_ids or set(q.requirement_ids) & set(task.requirement_ids)):
                blockers.add(q.id)
        # A clarification blocks only the work it directly affects. Downstream
        # work remains "waiting" on its task dependency, not misleadingly
        # "blocked" by an unrelated product decision.
        status = "blocked" if blockers else ("waiting" if task.dependencies else "ready")
        values = task.model_dump()
        values["blocked_by"] = sorted(blockers)
        finished[task_id] = Task(**values, order=order,
                                 wave=1 + max((finished[d].wave for d in task.dependencies), default=0),
                                 readiness=status, agent_prompt="")
    tasks = list(finished.values())
    coverage = []
    for req in extraction.document.requirements:
        relevant = [t for t in tasks if req.id in t.requirement_ids]
        coverage.append(Coverage(requirement_id=req.id, task_ids=[t.id for t in relevant],
                                 status="blocked" if any(t.readiness == "blocked" for t in relevant) else "planned"))
    plan = Plan(project_title=draft.project_title, summary=draft.summary,
                technical_context=draft.technical_context, assumptions=draft.assumptions,
                open_questions=questions, tasks=tasks, coverage=coverage)
    for task in tasks:
        task.agent_prompt = agent_prompt(task, plan, extraction, review)
    usage = generation_usage.model_copy(deep=True)
    usage.add(extraction.usage)
    warnings = list(extraction.warnings)
    warnings += ["Readiness means no known blocker, not that prerequisites were executed or a repository was inspected.",
                 "Requirement mapping is not proof that extraction captured every statement in the PDF."]
    if mode == "sample":
        warnings.insert(0, "Illustrative, manually authored sample. No Azure inference was used.")
    result = PlanResult(mode=mode, generated_at=datetime.now(timezone.utc).isoformat(), deployment=deployment,
                        extraction=extraction, review=review, plan=plan, usage=usage,
                        warnings=warnings, markdown="")
    result.markdown = markdown_export(result)
    return result
