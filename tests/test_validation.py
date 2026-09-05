import pytest

from planner.models import Answer, Correction, Question, Review, Usage
from planner.service import assemble_result
from planner.validation import (check_plan, check_requirements, check_review,
                                normalized, repair_requirement_evidence, topological_order)


def test_reference_requirements_have_actual_quotes(extraction):
    assert check_requirements(extraction.document, extraction.pages) == []


def test_invented_quote_is_rejected(extraction):
    extraction.document.requirements[0].evidence[0].quote = "Enable a crypto checkout, which the source never requested."
    assert "exact source quote" in " ".join(check_requirements(extraction.document, extraction.pages))


def test_near_verbatim_evidence_is_repaired_from_its_source_page(extraction):
    evidence = extraction.document.requirements[0].evidence[0]
    evidence.page = 1
    evidence.quote = "Team Notes is a single-workspace application for an internal team"
    repair_requirement_evidence(extraction.document, [
        {"page": page.number, "text": page.text} for page in extraction.pages
    ])
    assert normalized(evidence.quote) in normalized(extraction.pages[0].text)


def test_missing_source_page_is_rejected(extraction):
    extraction.document.requirements[0].evidence[0].page = 99
    assert "nonexistent page" in " ".join(check_requirements(extraction.document, extraction.pages))


def test_duplicate_requirement_ids_are_rejected(extraction):
    extraction.document.requirements[1].id = extraction.document.requirements[0].id
    assert "unique" in " ".join(check_requirements(extraction.document, extraction.pages))


def test_normalization_only_relaxes_whitespace_unicode():
    assert normalized("Hello\n  world\u00ad") == "Hello world"
    assert normalized("Hello") != normalized("hello")


def test_source_question_unknown_requirement_is_rejected(extraction):
    extraction.document.questions[0].requirement_ids = ["R-099"]
    assert "unknown requirement" in " ".join(check_requirements(extraction.document, extraction.pages))


def test_task_id_namespace_is_validated(draft, extraction):
    draft.tasks[0].id = "R-999"
    assert "T-###" in " ".join(check_plan(draft, extraction, Review()))


def test_reference_plan_has_valid_coverage_and_graph(draft, extraction):
    assert check_plan(draft, extraction, Review()) == []


@pytest.mark.parametrize("kind", ["missing", "self", "cycle", "duplicate"])
def test_invalid_dependencies_rejected(kind, draft, extraction):
    if kind == "missing": draft.tasks[0].dependencies = ["T-999"]
    elif kind == "self": draft.tasks[0].dependencies = ["T-001"]
    elif kind == "cycle": draft.tasks[0].dependencies = ["T-002"]
    else: draft.tasks[1].dependencies = ["T-001", "T-001"]
    assert check_plan(draft, extraction, Review())


def test_unknown_requirement_is_rejected(draft, extraction):
    draft.tasks[0].requirement_ids.append("R-099")
    assert "unknown requirement" in " ".join(check_plan(draft, extraction, Review()))


def test_missing_coverage_is_rejected(draft, extraction):
    for task in draft.tasks:
        task.requirement_ids = [r for r in task.requirement_ids if r != "R-008"]
    assert "R-008" in " ".join(check_plan(draft, extraction, Review()))


def test_answered_question_cannot_remain_a_blocker(draft, extraction):
    review = Review(answers=[Answer(question_id="Q-001", answer="Exact phrase")])
    assert "answered" in " ".join(check_plan(draft, extraction, review))


def test_additional_questions_cannot_overwrite_original(draft, extraction):
    draft.additional_questions = [extraction.document.questions[0]]
    assert "new, unique" in " ".join(check_plan(draft, extraction, Review()))


def test_sort_is_topological_not_merely_model_order(draft):
    draft.tasks.reverse()
    order = topological_order(draft)
    positions = {task_id: i for i, task_id in enumerate(order)}
    for task in draft.tasks:
        for dependency in task.dependencies:
            assert positions[dependency] < positions[task.id]


def test_blockers_apply_only_to_directly_affected_tasks(draft, extraction):
    for task in draft.tasks:
        task.blocked_by = []
    result = assemble_result(draft, extraction, Review(), "fixture", Usage(), "live")
    task_map = {t.id: t for t in result.plan.tasks}
    assert task_map["T-005"].blocked_by == ["Q-001"]
    assert task_map["T-008"].readiness == "blocked"  # It directly maps R-005.
    assert "STOP" in task_map["T-008"].agent_prompt
    assert task_map["T-001"].readiness == "ready"
    assert task_map["T-002"].readiness == "waiting"
    assert "T-001" in task_map["T-002"].agent_prompt


def test_global_blocker_blocks_all_tasks(draft, extraction):
    draft.additional_questions = [Question(id="Q-101", question="Which platform is required?", why_it_matters="Every implementation depends on this.", requirement_ids=[], blocking=True)]
    result = assemble_result(draft, extraction, Review(), "fixture", Usage(), "live")
    assert all(t.readiness == "blocked" and "Q-101" in t.blocked_by for t in result.plan.tasks)


def test_user_correction_is_separate_and_in_prompt(draft, extraction):
    review = Review(corrections=[Correction(requirement_id="R-001", description="Use a maximum title length of 100 characters, as my explicit correction.")])
    result = assemble_result(draft, extraction, review, "fixture", Usage(), "live")
    task = next(t for t in result.plan.tasks if t.id == "T-002")
    assert "user-corrected" in task.agent_prompt
    assert "100 characters" in task.agent_prompt
    assert "120 characters" in result.extraction.document.requirements[0].description


def test_review_requires_warning_acknowledgement(extraction):
    assert check_review(extraction, Review())
    assert not check_review(extraction, Review(acknowledge_warnings=True))


def test_review_rejects_duplicate_and_unknown_answers(extraction):
    answer = Answer(question_id="Q-099", answer="A supplied answer")
    review = Review(answers=[answer, answer], acknowledge_warnings=True)
    issues = " ".join(check_review(extraction, review))
    assert "one answer" in issues and "unknown question" in issues


def test_agent_prompt_contains_boundaries_and_completion_contract(draft, extraction):
    result = assemble_result(draft, extraction, Review(), "fixture", Usage(), "live")
    prompt = result.plan.tasks[0].agent_prompt
    for section in ["TRUST BOUNDARY", "ACCEPTANCE CRITERIA", "TEST SCENARIOS", "OUT OF SCOPE", "COMPLETION CONTRACT", "first principles"]:
        assert section in prompt
    assert "Never report a test as passing unless it ran" in prompt
