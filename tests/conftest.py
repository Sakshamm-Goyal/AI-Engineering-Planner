import json

import pytest
from fastapi.testclient import TestClient

from planner.app import create_app
from planner.config import ROOT, Settings
from planner.models import Extraction, PlanDraft, RequirementsDocument, Usage, VisionPage


@pytest.fixture
def settings():
    return Settings(_env_file=None, azure_openai_endpoint="https://test-resource.openai.azure.com",
                    azure_openai_api_key="fixture-not-a-real-key", azure_openai_deployment="my-luna-deployment")


@pytest.fixture
def extraction():
    return Extraction.model_validate_json((ROOT / "examples/reference-extraction.json").read_text()).model_copy(update={"mode": "live"})


@pytest.fixture
def draft():
    return PlanDraft.model_validate_json((ROOT / "examples/reference-plan.json").read_text())


class FixtureProvider:
    """TEST ONLY: authored responses; not evidence of real model performance."""
    def __init__(self):
        self.extraction = Extraction.model_validate_json((ROOT / "examples/reference-extraction.json").read_text())
        self.draft = PlanDraft.model_validate_json((ROOT / "examples/reference-plan.json").read_text())
        self.calls = []
        self.unreadable = False

    async def generate(self, model, instruction, payload, image=None):
        self.calls.append((model, instruction, payload, image))
        if model is VisionPage:
            page = payload.get("page_number", 1)
            data = {"text": self.extraction.pages[page - 1].text, "visual_notes": "",
                    "readable": not self.unreadable, "warnings": []}
        elif model is RequirementsDocument:
            data = self.extraction.document.model_dump()
        elif model is PlanDraft:
            data = self.draft.model_dump()
            answered = {a["question_id"] for a in payload.get("user_answers", [])}
            for task in data["tasks"]:
                task["blocked_by"] = [q for q in task["blocked_by"] if q not in answered]
        else:
            raise AssertionError(f"Unexpected schema {model}")
        return json.loads(json.dumps(data)), Usage(calls=1, input_tokens=100, output_tokens=50)

    async def close(self):
        pass


@pytest.fixture
def provider():
    return FixtureProvider()


@pytest.fixture
def client(settings, provider):
    with TestClient(create_app(settings, provider=provider)) as c:
        yield c
