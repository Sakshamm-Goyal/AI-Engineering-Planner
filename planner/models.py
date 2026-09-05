from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[RQT]-\d{3}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(Model):
    calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


class Page(Model):
    number: int = Field(ge=1, le=500)
    text: str = Field(max_length=100000)
    native_text: str = Field(max_length=100000)
    method: Literal["native", "ocr", "vision"]
    warnings: list[ShortText] = Field(max_length=30)


class Evidence(Model):
    page: int = Field(ge=1, le=500)
    quote: Text


class Requirement(Model):
    id: Identifier
    title: ShortText
    description: Text
    kind: Literal["functional", "non_functional", "constraint"]
    acceptance_criteria: list[Text] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class Question(Model):
    id: Identifier
    question: ShortText
    why_it_matters: Text
    requirement_ids: list[Identifier] = Field(max_length=100)
    blocking: bool


class RequirementsDocument(Model):
    title: ShortText
    summary: Text
    requirements: list[Requirement] = Field(default_factory=list, max_length=100)
    questions: list[Question] = Field(max_length=30)
    out_of_scope: list[Text] = Field(max_length=30)


class Extraction(Model):
    document_id: str = Field(min_length=1, max_length=100)
    filename: ShortText
    pages: list[Page] = Field(min_length=1, max_length=100)
    document: RequirementsDocument
    warnings: list[ShortText] = Field(max_length=100)
    usage: Usage
    mode: Literal["live", "sample"]


class Correction(Model):
    requirement_id: Identifier
    description: Text


class Answer(Model):
    question_id: Identifier
    answer: Text


class Review(Model):
    implementation_context: str = Field(default="", max_length=12000)
    corrections: list[Correction] = Field(default_factory=list, max_length=100)
    answers: list[Answer] = Field(default_factory=list, max_length=30)
    acknowledge_warnings: bool = False


class PlanRequest(Model):
    extraction: Extraction
    review: Review


class Decision(Model):
    decision: ShortText
    rationale: Text
    basis: Literal["prd", "user_context", "proposed"]


class TechnicalContext(Model):
    stack: list[ShortText] = Field(min_length=1, max_length=12)
    approach: Text
    interfaces: list[Text] = Field(max_length=20)
    decisions: list[Decision] = Field(max_length=20)


class TaskDraft(Model):
    id: Identifier
    title: ShortText
    description: Text
    requirement_ids: list[Identifier] = Field(min_length=1, max_length=100)
    dependencies: list[Identifier] = Field(max_length=60)
    complexity: Literal["S", "M", "L"]
    complexity_reason: ShortText
    release_target: ShortText = "Unscheduled"
    priority: Literal["must", "should", "could", "unspecified"] = "unspecified"
    implementation_steps: list[Text] = Field(min_length=1, max_length=10)
    acceptance_criteria: list[Text] = Field(min_length=1)
    test_cases: list[Text] = Field(min_length=1)
    out_of_scope: list[Text] = Field(max_length=12)
    blocked_by: list[Identifier] = Field(max_length=30)


class PlanDraft(Model):
    project_title: ShortText
    summary: Text
    technical_context: TechnicalContext
    assumptions: list[Text] = Field(max_length=20)
    # Only newly discovered questions. Existing unanswered questions are merged by code.
    additional_questions: list[Question] = Field(max_length=30)
    tasks: list[TaskDraft] = Field(min_length=1, max_length=60)


class Task(TaskDraft):
    order: int = Field(ge=1)
    wave: int = Field(ge=1)
    readiness: Literal["ready", "waiting", "blocked"]
    agent_prompt: str


class Coverage(Model):
    requirement_id: Identifier
    task_ids: list[Identifier]
    status: Literal["planned", "blocked"]


class Plan(Model):
    project_title: str
    summary: str
    technical_context: TechnicalContext
    assumptions: list[str]
    open_questions: list[Question]
    tasks: list[Task]
    coverage: list[Coverage]


class PlanResult(Model):
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["live", "sample"]
    generated_at: str
    deployment: str
    extraction: Extraction
    review: Review
    plan: Plan
    usage: Usage
    warnings: list[str]
    markdown: str


class VisionPage(Model):
    text: str = Field(max_length=100000)
    visual_notes: str = Field(max_length=20000)
    readable: bool
    warnings: list[ShortText] = Field(max_length=20)
