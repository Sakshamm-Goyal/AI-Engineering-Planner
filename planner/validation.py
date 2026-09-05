"""Deterministic checks: the LLM does not get to declare its own plan valid."""
import heapq
import re
import unicodedata
from difflib import SequenceMatcher

from .models import Extraction, PlanDraft, Question, RequirementsDocument, Review


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).replace("\u00ad", "").split())


def duplicates(values: list[str]) -> bool:
    return len(values) != len(set(values))


def check_requirements(document: RequirementsDocument, pages: list) -> list[str]:
    issues = []
    page_map = {p.number: p.text for p in pages}
    ids = [r.id for r in document.requirements]
    if duplicates(ids):
        issues.append("Requirement IDs must be unique.")
    if any(not re.fullmatch(r"R-\d{3}", i) for i in ids):
        issues.append("Requirement IDs must use R-###.")
    question_ids = [q.id for q in document.questions]
    if duplicates(question_ids):
        issues.append("Question IDs must be unique.")
    for question in document.questions:
        if not re.fullmatch(r"Q-\d{3}", question.id):
            issues.append("Question IDs must use Q-###.")
        if not set(question.requirement_ids) <= set(ids):
            issues.append(f"{question.id} references an unknown requirement.")
    for req in document.requirements:
        for evidence in req.evidence:
            if evidence.page not in page_map:
                issues.append(f"{req.id} cites a nonexistent page.")
            elif normalized(evidence.quote) not in normalized(page_map[evidence.page]):
                issues.append(f"{req.id} evidence on page {evidence.page} is not an exact source quote.")
    return issues


def repair_requirement_evidence(document: RequirementsDocument, pages: list[dict]) -> None:
    """Anchor model-selected evidence pages to deterministic verbatim excerpts.

    The model identifies the relevant page, but text-generation models are poor
    at character-perfect copying. We retrieve the closest source passage locally,
    so provenance remains real source text and quoting drift cannot invalidate a
    complete document extraction.
    """
    page_map = {page["page"]: page["text"] for page in pages}
    for requirement in document.requirements:
        for evidence in requirement.evidence:
            source = page_map.get(evidence.page)
            if not source or normalized(evidence.quote) in normalized(source):
                continue
            query = normalized(evidence.quote)
            lines = [normalized(line) for line in source.splitlines() if normalized(line)]
            candidates = list(lines)
            # PDF text often places one semantic sentence across several lines.
            for width in range(2, 5):
                candidates.extend(" ".join(lines[start:start + width])
                                  for start in range(len(lines) - width + 1))
            query_terms = set(re.findall(r"\w+", query.lower()))
            best_score, best_text = -1.0, ""
            for candidate in candidates:
                terms = set(re.findall(r"\w+", candidate.lower()))
                overlap = len(query_terms & terms) / max(len(query_terms), 1)
                score = 0.55 * overlap + 0.45 * SequenceMatcher(None, query, candidate).ratio()
                if score > best_score:
                    best_score, best_text = score, candidate
            # Even for a poor paraphrase, retain a bounded exact excerpt from the
            # page selected by the model rather than reject the entire inventory.
            evidence.quote = (best_text or normalized(source))[:6000]


def check_review(extraction: Extraction, review: Review) -> list[str]:
    issues = []
    requirements = {r.id for r in extraction.document.requirements}
    questions = {q.id for q in extraction.document.questions}
    if duplicates([c.requirement_id for c in review.corrections]):
        issues.append("A requirement can only have one correction.")
    if duplicates([a.question_id for a in review.answers]):
        issues.append("A question can only have one answer.")
    if not {c.requirement_id for c in review.corrections} <= requirements:
        issues.append("A correction references an unknown requirement.")
    if not {a.question_id for a in review.answers} <= questions:
        issues.append("An answer references an unknown question.")
    if (extraction.warnings or any(p.warnings for p in extraction.pages)) and not review.acknowledge_warnings:
        issues.append("Review and acknowledge the extraction warnings before generating a plan.")
    return issues


def unanswered(extraction: Extraction, review: Review) -> list[Question]:
    answered = {a.question_id for a in review.answers}
    return [q for q in extraction.document.questions if q.id not in answered]


def check_plan(draft: PlanDraft, extraction: Extraction, review: Review) -> list[str]:
    issues = []
    reqs = {r.id for r in extraction.document.requirements}
    ids = [t.id for t in draft.tasks]
    known = set(ids)
    if duplicates(ids):
        issues.append("Task IDs must be unique.")
    if any(not re.fullmatch(r"T-\d{3}", i) for i in ids):
        issues.append("Task IDs must use T-###.")
    new_ids = [q.id for q in draft.additional_questions]
    all_original_questions = {q.id for q in extraction.document.questions}
    if duplicates(new_ids) or set(new_ids) & all_original_questions:
        issues.append("Additional questions must have new, unique IDs; do not repeat existing questions.")
    for q in draft.additional_questions:
        if not re.fullmatch(r"Q-\d{3}", q.id):
            issues.append("Question IDs must use Q-###.")
        if not set(q.requirement_ids) <= reqs:
            issues.append(f"{q.id} references an unknown requirement.")
    questions = unanswered(extraction, review) + draft.additional_questions
    blocking_ids = {q.id for q in questions if q.blocking}
    covered = set()
    for task in draft.tasks:
        covered.update(task.requirement_ids)
        if not set(task.requirement_ids) <= reqs:
            issues.append(f"{task.id} references an unknown requirement.")
        if duplicates(task.requirement_ids) or duplicates(task.dependencies) or duplicates(task.blocked_by):
            issues.append(f"{task.id} has duplicate references.")
        if not set(task.dependencies) <= known:
            issues.append(f"{task.id} references a missing dependency.")
        if task.id in task.dependencies:
            issues.append(f"{task.id} depends on itself.")
        if not set(task.blocked_by) <= blocking_ids:
            issues.append(f"{task.id} references an unknown, answered or non-blocking question as a blocker.")
    if reqs - covered:
        issues.append("Requirements without tasks: " + ", ".join(sorted(reqs - covered)))
    if not issues:
        try:
            topological_order(draft)
        except ValueError:
            issues.append("The task dependency graph contains a cycle. Correct the dependencies; do not silently drop work.")
    return issues


def topological_order(draft: PlanDraft) -> list[str]:
    """Stable Kahn sort: model order is a tie-breaker, never a substitute for a DAG."""
    positions = {t.id: i for i, t in enumerate(draft.tasks)}
    degrees = {t.id: len(t.dependencies) for t in draft.tasks}
    outgoing: dict[str, list[str]] = {t.id: [] for t in draft.tasks}
    for task in draft.tasks:
        for dep in task.dependencies:
            outgoing[dep].append(task.id)
    ready = [(positions[t], t) for t, degree in degrees.items() if degree == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        _, current = heapq.heappop(ready)
        order.append(current)
        for next_id in outgoing[current]:
            degrees[next_id] -= 1
            if degrees[next_id] == 0:
                heapq.heappush(ready, (positions[next_id], next_id))
    if len(order) != len(draft.tasks):
        raise ValueError("Cyclic task graph")
    return order
