"""Deterministic agent prompts and Markdown exports from validated data."""
from .config import ROOT
from .models import Extraction, Plan, PlanResult, Review, Task

AGENT_TEMPLATE = (ROOT / "planner/prompts/agent.txt").read_text(encoding="utf-8")


def bullets(values: list[str]) -> str:
    return "\n".join(f"- {v}" for v in values) or "- None."


def agent_prompt(task: Task, plan: Plan, extraction: Extraction, review: Review) -> str:
    corrections = {c.requirement_id: c.description for c in review.corrections}
    requirements = []
    for req in extraction.document.requirements:
        if req.id not in task.requirement_ids:
            continue
        description = corrections.get(req.id, req.description)
        annotation = " [user-corrected; original evidence retained]" if req.id in corrections else ""
        evidence = "; ".join(f"page {e.page}: {e.quote}" for e in req.evidence)
        requirements.append(f"{req.id} — {req.title}{annotation}: {description}\n  Source: {evidence}")
    task_map = {t.id: t for t in plan.tasks}
    dependencies = [f"{i} — {task_map[i].title}. Confirm it is implemented and tested." for i in task.dependencies]
    if task.readiness == "blocked":
        questions = {q.id: q for q in plan.open_questions}
        readiness = "STOP — THIS TASK IS BLOCKED. Do not implement it until these questions are resolved:\n" + bullets([f"{q}: {questions[q].question}" for q in task.blocked_by])
    elif task.dependencies:
        readiness = "This task is waiting on prerequisite work. Begin only after the dependencies below are complete."
    else:
        readiness = "This task has no known blocking question or task prerequisite. Still verify the repository and stated assumptions first."
    context = (
        f"{plan.project_title}\n{plan.summary}\n\nStack: {', '.join(plan.technical_context.stack)}"
        f"\nApproach: {plan.technical_context.approach}"
        f"\nShared interfaces:\n{bullets(plan.technical_context.interfaces)}"
        f"\nDecisions:\n{bullets([f'{d.decision} [{d.basis}]. {d.rationale}' for d in plan.technical_context.decisions])}"
        f"\nExplicit assumptions (not PRD facts):\n{bullets(plan.assumptions)}"
    )
    if review.implementation_context:
        context += "\nUser implementation context:\n" + review.implementation_context
    if review.answers:
        questions = {q.id: q.question for q in extraction.document.questions}
        context += "\nUser clarifications:\n" + bullets([f"{a.question_id} ({questions[a.question_id]}): {a.answer}" for a in review.answers])
    return AGENT_TEMPLATE.format(
        task_id=task.id, title=task.title, readiness=readiness, project_context=context,
        dependencies=bullets(dependencies), description=task.description,
        requirements=bullets(requirements), steps=bullets(task.implementation_steps),
        acceptance=bullets(task.acceptance_criteria), tests=bullets(task.test_cases),
        out_of_scope=bullets(task.out_of_scope + extraction.document.out_of_scope),
    )


def fence(text: str) -> str:
    """Choose a fence longer than any run of backticks in untrusted content."""
    import re
    length = max([len(m) for m in re.findall(r"`+", text)] + [2]) + 1
    ticks = "`" * length
    return f"{ticks}text\n{text}\n{ticks}"


def markdown_export(result: PlanResult) -> str:
    plan = result.plan
    lines = [f"# {plan.project_title}", "", plan.summary, "",
             f"Mode: **{result.mode}** | Deployment: `{result.deployment}` | Generated: {result.generated_at}",
             "", "This is an implementation plan, not evidence that code exists or tests have passed.",
             "Task mapping covers extracted requirements, not independently verified completeness of the PDF.", ""]
    if result.mode == "sample":
        lines += ["> Illustrative, manually authored sample. No Azure inference produced this output.", ""]
    lines += ["## Reading notes", bullets(result.warnings), "", "## Implementation context",
              plan.technical_context.approach, "", "Stack: " + ", ".join(plan.technical_context.stack), "",
              "### Shared interfaces", bullets(plan.technical_context.interfaces), "", "### Decisions",
              bullets([f"{d.decision} ({d.basis}): {d.rationale}" for d in plan.technical_context.decisions]),
              "", "### Assumptions", bullets(plan.assumptions), "", "## Open questions",
              bullets([f"{q.id}: {q.question} — {'blocking' if q.blocking else 'non-blocking'}. {q.why_it_matters}" for q in plan.open_questions]),
              "", "## Review record", "User implementation context: " + (result.review.implementation_context or "Not supplied."),
              bullets([f"Correction to {c.requirement_id}: {c.description}" for c in result.review.corrections]),
              bullets([f"Answer to {a.question_id}: {a.answer}" for a in result.review.answers]),
              "", "## Requirement mapping"]
    for row in plan.coverage:
        lines.append(f"- {row.requirement_id}: {', '.join(row.task_ids)} ({row.status})")
    lines += ["", "## Source requirements"]
    for req in result.extraction.document.requirements:
        lines += [f"### {req.id} — {req.title}", req.description,
                  bullets([f"Page {e.page}: {e.quote}" for e in req.evidence]), ""]
    lines += ["## Ordered tasks"]
    for task in plan.tasks:
        lines += [f"### {task.order}. {task.id} — {task.title}", "", task.description, "",
                  f"Complexity: **{task.complexity}** — {task.complexity_reason}",
                  f"Release: **{task.release_target}** | Priority: **{task.priority}**",
                  f"Readiness: **{task.readiness}** | Dependency wave: {task.wave}",
                  "Dependencies: " + (", ".join(task.dependencies) or "None"),
                  "Requirements: " + ", ".join(task.requirement_ids),
                  "Blocking questions: " + (", ".join(task.blocked_by) or "None"), "",
                  "#### Implementation outline", bullets(task.implementation_steps), "",
                  "#### Acceptance criteria", bullets(task.acceptance_criteria), "",
                  "#### Test scenarios", bullets(task.test_cases), "",
                  "#### Out of scope", bullets(task.out_of_scope), "",
                  "#### Coding-agent prompt", fence(task.agent_prompt), ""]
    return "\n".join(lines)
