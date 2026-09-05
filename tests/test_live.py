"""Opt-in, billable end-to-end inference. Never enabled by ordinary tests or CI."""
import asyncio
import os

import pytest

from planner.azure import AzureResponses
from planner.config import ROOT, Settings
from planner.models import PlanRequest, Review
from planner.service import PlannerService

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("RUN_AZURE_LIVE") != "1", reason="Real Azure inference is opt-in and billable.")]


async def test_actual_azure_native_pdf_to_plan():
    settings = Settings()
    assert settings.configured, "Set Azure credentials and deployment in .env before opting in."
    provider = AzureResponses(settings)
    try:
        service = PlannerService(settings, provider)
        async with asyncio.timeout(settings.pipeline_timeout_seconds):
            extraction = await service.extract((ROOT / "examples/team-notes-prd.pdf").read_bytes(), "team-notes-prd.pdf", "", "auto")
            result = await service.generate_plan(PlanRequest(extraction=extraction, review=Review(acknowledge_warnings=True)))
        assert result.mode == "live" and result.usage.calls >= 2
        assert result.plan.tasks
        assert len(result.plan.coverage) == len(extraction.document.requirements)
        order = {task.id: task.order for task in result.plan.tasks}
        assert all(order[dep] < task.order for task in result.plan.tasks for dep in task.dependencies)
        assert all(task.agent_prompt for task in result.plan.tasks)
        # No fixed task count or self-graded semantic accuracy claim.
    finally:
        await provider.close()
