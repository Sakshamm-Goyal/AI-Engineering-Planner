import json

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from planner.azure import AzureResponses, provider_diagnostic, strict_schema
from planner.config import Settings
from planner.errors import AppError
from planner.models import PlanDraft, RequirementsDocument, VisionPage


def packet(data=None, status="completed", content=None):
    return {"status": status, "output": [{"type": "message", "content": content or [{"type": "output_text", "text": json.dumps(data or {})}]}], "usage": {"input_tokens": 123, "output_tokens": 45}}


@pytest.mark.parametrize("endpoint", ["https://abc.openai.azure.com", "https://abc.openai.azure.com/openai/v1/", "https://abc.services.ai.azure.com/"])
def test_endpoint_normalization(endpoint):
    settings = Settings(_env_file=None, azure_openai_endpoint=endpoint)
    assert settings.azure_openai_endpoint.endswith("/openai/v1/")
    assert settings.azure_openai_endpoint.count("/openai/v1/") == 1


@pytest.mark.parametrize("endpoint", ["http://abc.openai.azure.com", "https://user:password@host", "https://host/other/path", "https://host?api-key=secret"])
def test_invalid_endpoint_rejected(endpoint):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, azure_openai_endpoint=endpoint)


@pytest.mark.parametrize("model", [VisionPage, RequirementsDocument, PlanDraft])
def test_schema_is_strict_and_preserves_title_property(model):
    schema = strict_schema(model)
    Draft202012Validator.check_schema(schema)
    def walk(node):
        if isinstance(node, list):
            for child in node: walk(child)
        elif isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for key, value in node.items():
                if key in ("properties", "$defs"):
                    for child in value.values(): walk(child)
                else:
                    assert key not in ("minLength", "maxLength", "pattern", "minimum", "maxItems", "default")
                    walk(value)
    walk(schema)
    if model is RequirementsDocument:
        assert "title" in schema["properties"]
        assert "title" in schema["$defs"]["Requirement"]["properties"]


async def test_azure_request_uses_deployment_store_false_and_images(settings):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=packet({"text": "visible text", "visual_notes": "", "readable": True, "warnings": []}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = AzureResponses(settings, client)
        data, usage = await provider.generate(VisionPage, "Read faithfully.", {"page": 1}, "cG5n")
    body = json.loads(seen[0].content)
    assert str(seen[0].url).endswith("/openai/v1/responses")
    assert seen[0].headers["api-key"] == "fixture-not-a-real-key"
    assert body["model"] == "my-luna-deployment"
    assert body["store"] is False and body["truncation"] == "disabled"
    assert "temperature" not in body and "reasoning" not in body
    assert body["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert body["text"]["format"]["strict"] is True
    assert data["readable"] is True and usage.input_tokens == 123


async def test_azure_falls_back_when_strict_schema_or_budget_is_rejected(settings):
    formats = []

    def handle(request):
        body = json.loads(request.content)
        formats.append(body.get("text", {}).get("format", {}).get("type", "plain"))
        if len(formats) < 3:
            return httpx.Response(400, json={"error": {"code": "unsupported_parameter"}})
        return httpx.Response(200, json=packet({
            "text": "visible text", "visual_notes": "", "readable": True, "warnings": [],
        }))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = AzureResponses(settings, client)
        data, _ = await provider.generate(VisionPage, "Read faithfully.", {"page": 1})
        assert data["readable"] is True
        await provider.generate(VisionPage, "Read faithfully.", {"page": 2})

    assert formats[:3] == ["json_schema", "json_schema", "json_object"]
    assert formats[3] == "json_object"


async def test_azure_falls_back_to_chat_completions_when_responses_is_unavailable(settings):
    paths = []
    chat_bodies = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(400, json={"error": {"code": "unsupported_api"}})
        chat_bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({
                "text": "visible text", "visual_notes": "", "readable": True, "warnings": [],
            })}}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 45},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = AzureResponses(settings, client)
        data, usage = await provider.generate(VisionPage, "Read faithfully.", {"page": 1})

    assert data["readable"] is True
    assert usage.input_tokens == 123
    assert paths[-1].endswith("/chat/completions")
    assert chat_bodies[0]["max_completion_tokens"] == settings.max_output_tokens


@pytest.mark.parametrize("status,code", [(401, "azure_auth"), (403, "azure_auth"), (404, "azure_deployment"), (400, "azure_request")])
async def test_provider_errors_are_actionable_and_do_not_echo_body(settings, status, code):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status, json={"error": "sensitive document contents and secret"}))) as client:
        with pytest.raises(AppError) as error:
            await AzureResponses(settings, client).generate(VisionPage, "instruction", {})
    assert error.value.code == code
    assert "sensitive" not in error.value.message
    assert "secret" not in error.value.message


def test_provider_diagnostic_keeps_identifiers_without_echoing_provider_prose():
    response = httpx.Response(400, json={"error": {
        "code": "unsupported_parameter",
        "param": "max_output_tokens",
        "message": "The sensitive PRD says do not disclose this text.",
    }})
    diagnostic = provider_diagnostic(response)
    assert "code=unsupported_parameter" in diagnostic
    assert "param=max_output_tokens" in diagnostic
    assert "sensitive" not in diagnostic
    assert "PRD" not in diagnostic


async def test_refusal_is_not_treated_as_a_plan(settings):
    response = packet(content=[{"type": "refusal", "refusal": "declined"}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))) as client:
        with pytest.raises(AppError, match="declined"):
            await AzureResponses(settings, client).generate(VisionPage, "instruction", {})


async def test_incomplete_output_is_not_accepted(settings):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=packet(status="incomplete")))) as client:
        with pytest.raises(AppError) as error:
            await AzureResponses(settings, client).generate(VisionPage, "instruction", {})
    assert error.value.code == "azure_incomplete"


async def test_rate_limit_retried_once_then_surfaced(settings):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(429, headers={"retry-after": "0"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AppError) as error:
            await AzureResponses(settings, client).generate(VisionPage, "instruction", {})
    assert count == 2 and error.value.code == "azure_rate_limit"


async def test_transient_retry_can_succeed(settings):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(503, headers={"retry-after": "0"}) if count == 1 else httpx.Response(200, json=packet())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, usage = await AzureResponses(settings, client).generate(VisionPage, "instruction", {})
    assert count == 2 and usage.calls == 1


async def test_timeout_not_retried(settings):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("sensitive request", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AppError) as error:
            await AzureResponses(settings, client).generate(VisionPage, "instruction", {})
    assert count == 1 and error.value.code == "azure_timeout"
    assert "sensitive" not in error.value.message


async def test_malformed_output_can_be_repaired_by_service(settings):
    response = packet(content=[{"type": "output_text", "text": "not JSON"}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))) as client:
        data, _ = await AzureResponses(settings, client).generate(VisionPage, "instruction", {})
    assert "_invalid_json_response" in data


async def test_text_budget_is_checked_before_network(settings):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: pytest.fail("Must not call Azure"))) as client:
        with pytest.raises(AppError) as error:
            await AzureResponses(settings, client).generate(VisionPage, "instruction", {"text": "a" * 400000})
    assert error.value.code == "context_limit"


async def test_no_credentials_is_not_an_automatic_demo_fallback():
    provider = AzureResponses(Settings(_env_file=None, azure_openai_endpoint="", azure_openai_api_key=""))
    try:
        with pytest.raises(AppError) as error:
            await provider.generate(VisionPage, "instruction", {})
        assert error.value.code == "azure_not_configured"
    finally:
        await provider.close()


@pytest.mark.parametrize("body", [[], {"status": "completed", "output": None},
    {"status": "completed", "output": ["not an item"]},
    {"status": "completed", "output": [{"content": ["not a part"]}]},
    {"status": "completed", "output": [{"content": [{"type": "output_text", "text": 123}]}]},
    {"status": "completed", "usage": {"input_tokens": -1}},
    {"status": "completed", "usage": "invalid"}])
async def test_invalid_provider_envelopes_fail_safely(settings, body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as client:
        with pytest.raises(AppError) as error:
            await AzureResponses(settings, client).generate(VisionPage, "instruction", {})
    assert error.value.code == "azure_protocol"
