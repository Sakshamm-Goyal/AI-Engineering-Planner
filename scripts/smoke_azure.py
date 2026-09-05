"""Explicitly opt in to a real, billable Azure run and save its actual output.

This is not a mock or automatic setup check. The PDF's content is sent to Azure.
Usage: python scripts/smoke_azure.py --allow-billable-calls
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from planner.azure import AzureResponses
from planner.config import Settings
from planner.errors import AppError
from planner.models import PlanRequest, Review
from planner.service import PlannerService


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    if not settings.configured:
        raise AppError("azure_not_configured", "Set the Azure endpoint, key and deployment in .env first.")
    pdf = Path(args.pdf)
    with pdf.open("rb") as source:
        content = source.read(settings.max_file_bytes + 1)
    if len(content) > settings.max_file_bytes:
        raise AppError("file_limit", f"The input exceeds {settings.max_file_mb} MB.")
    provider = AzureResponses(settings)
    try:
        service = PlannerService(settings, provider)
        print(f"Sending {pdf.name} to the configured Azure deployment. This run may incur charges.")
        async with asyncio.timeout(settings.pipeline_timeout_seconds):
            extraction = await service.extract(content, pdf.name, args.context, args.vision_mode)
            print(f"Extracted {len(extraction.document.requirements)} requirements; generating the plan.")
            result = await service.generate_plan(PlanRequest(
                extraction=extraction,
                review=Review(implementation_context=args.context, acknowledge_warnings=True),
            ))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
        output = Path(args.output_dir) / stamp
        output.mkdir(parents=True, exist_ok=False)
        (output / "extraction.json").write_text(extraction.model_dump_json(indent=2), encoding="utf-8")
        (output / "plan.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        (output / "plan.md").write_text(result.markdown, encoding="utf-8")
        print(json.dumps({"tasks": len(result.plan.tasks),
                          "blocked_tasks": sum(t.readiness == "blocked" for t in result.plan.tasks),
                          "recorded_usage": result.usage.model_dump(), "output_directory": str(output)}, indent=2))
        print("Inspect the source evidence and plan before execution. Structural validity is not semantic accuracy.")
        print("These exports contain document content. Protect or delete them when finished.")
    finally:
        await provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-billable-calls", action="store_true", help="I consent to send this PDF to Azure and incur inference charges.")
    parser.add_argument("--pdf", default=str(ROOT / "examples/team-notes-prd.pdf"))
    parser.add_argument("--context", default="")
    parser.add_argument("--vision-mode", choices=("auto", "all"), default="auto")
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts/live"))
    args = parser.parse_args()
    if not args.allow_billable_calls:
        parser.error("Real inference is disabled. Pass --allow-billable-calls only after reviewing the privacy and billing implications.")
    try:
        asyncio.run(run(args))
    except (AppError, OSError, TimeoutError, ValueError) as exc:
        message = exc.message if isinstance(exc, AppError) else str(exc)
        print(f"Smoke test failed: {message or type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
