"""Small Azure Responses REST adapter. No orchestration framework or SDK shim.

Schema, errors, retries, refusals and token usage are explicit and testable using
httpx.MockTransport. The API key never leaves this backend.
"""
import asyncio
import copy
import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings
from .errors import AppError
from .models import Usage

# Azure supports a JSON Schema subset. Keep validation constraints in Pydantic,
# but strip unsupported keywords from the schema submitted to the provider.
_UNSUPPORTED = {
    "title", "default", "examples", "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
}


def provider_diagnostic(response: httpx.Response) -> str:
    """Return only Azure's stable error identifiers, never provider prose.

    Azure validation messages can include user-supplied values. Error codes and
    parameter names are enough to diagnose an unsupported request feature
    without returning document-derived content to the browser.
    """
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return ""
    if not isinstance(error, dict):
        return ""
    fields = []
    for key in ("code", "param", "type"):
        value = error.get(key)
        if isinstance(value, str) and value.replace("_", "").replace("-", "").isalnum():
            fields.append(f"{key}={value}")
    return f" Azure diagnostic: {', '.join(fields)}." if fields else ""


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {}
        for key, value in node.items():
            if key in _UNSUPPORTED:
                continue
            # Property names are data, not JSON Schema keywords.
            if key in ("properties", "$defs"):
                result[key] = {name: clean(schema) for name, schema in value.items()}
            else:
                result[key] = clean(value)
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result
    return clean(copy.deepcopy(model.model_json_schema()))


class AzureResponses:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10),
            follow_redirects=False,
        )
        self.owns_client = client is None
        self.response_format = "strict"
        self.output_budget = settings.max_output_tokens
        self.api_mode = "responses"
        self.chat_response_format = "json_object"
        self.chat_output_budget = settings.max_output_tokens
        self.chat_token_parameter = "max_completion_tokens"

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def _chat_fallback(self, instruction: str, text: str,
                             image: str | None) -> tuple[dict, Usage] | httpx.Response:
        """Use Azure's v1 Chat Completions endpoint when Responses is unavailable."""
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if image:
            content.append({"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{image}", "detail": "high",
            }})
        attempts = [(self.chat_response_format, self.chat_output_budget, self.chat_token_parameter)]
        for response_format in ("json_object", "plain"):
            for output_budget in (self.settings.max_output_tokens, 4096):
                for token_parameter in ("max_completion_tokens", "max_tokens"):
                    candidate = (response_format, output_budget, token_parameter)
                    if candidate not in attempts:
                        attempts.append(candidate)

        response: httpx.Response | None = None
        for response_format, output_budget, token_parameter in attempts:
            body: dict[str, Any] = {
                "model": self.settings.azure_openai_deployment,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": content},
                ],
                token_parameter: output_budget,
            }
            if response_format == "json_object":
                body["response_format"] = {"type": "json_object"}
            for retry in range(2):
                try:
                    async with asyncio.timeout(self.settings.llm_timeout_seconds):
                        response = await self.client.post(
                            self.settings.azure_openai_endpoint + "chat/completions", json=body,
                            headers={"api-key": self.settings.azure_openai_api_key.get_secret_value()},
                        )
                except (httpx.TimeoutException, TimeoutError) as exc:
                    raise AppError("azure_timeout", "Azure did not respond within the configured timeout. Try a smaller document or review the deployment capacity.", 504) from exc
                except httpx.RequestError as exc:
                    raise AppError("azure_connection", "The backend could not reach the Azure endpoint. Check networking and endpoint configuration.", 502) from exc
                if response.status_code in (429, 502, 503, 504) and retry == 0:
                    try:
                        delay = min(max(float(response.headers.get("retry-after", "1")), 0), 5)
                    except ValueError:
                        delay = 1
                    await asyncio.sleep(delay)
                    continue
                break
            if response.status_code != 400:
                break
        assert response is not None
        if response.is_error:
            return response
        try:
            data = response.json()
            choices = data.get("choices", [])
            message = choices[0].get("message", {}) if isinstance(choices, list) and choices else {}
            output_text = message.get("content") if isinstance(message, dict) else None
            usage_data = data.get("usage") or {}
            usage = Usage(calls=1, input_tokens=usage_data.get("prompt_tokens", 0),
                          output_tokens=usage_data.get("completion_tokens", 0))
            if not isinstance(output_text, str):
                raise ValueError("missing content")
            parsed = json.loads(output_text)
            if not isinstance(parsed, dict):
                return {"_invalid_root": parsed}, usage
        except (ValueError, TypeError, IndexError, ValidationError) as exc:
            raise AppError("azure_protocol", "Azure Chat Completions returned an invalid response envelope.", 502) from exc
        self.api_mode = "chat"
        self.chat_response_format, self.chat_output_budget, self.chat_token_parameter = (
            response_format, output_budget, token_parameter,
        )
        return parsed, usage

    async def generate(self, model: type[BaseModel], instruction: str,
                       payload: dict[str, Any], image: str | None = None) -> tuple[dict, Usage]:
        if not self.settings.configured:
            raise AppError("azure_not_configured", "Configure the Azure endpoint, API key and deployment in .env. The sample remains available.", 503)
        text = json.dumps(payload, ensure_ascii=False)
        schema = strict_schema(model)
        request_bytes = len((instruction + text + json.dumps(schema)).encode("utf-8"))
        if request_bytes > self.settings.max_input_text_bytes:
            raise AppError("context_limit", "This request exceeds the configured text budget. Split the PRD or reduce the review context; nothing was truncated.")
        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        if image:
            if len(image) > 12 * 1024 * 1024:
                raise AppError("image_limit", "The rendered page exceeds the image payload limit.")
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{image}", "detail": "high"})
        if self.api_mode == "chat":
            fallback = await self._chat_fallback(instruction, text, image)
            if isinstance(fallback, tuple):
                return fallback
            response = fallback
            return self._raise_provider_error(response)

        attempts = [(self.response_format, self.output_budget)]
        for response_format in ("strict", "json_object", "plain"):
            for output_budget in (self.settings.max_output_tokens, 4096):
                candidate = (response_format, output_budget)
                if candidate not in attempts:
                    attempts.append(candidate)

        response: httpx.Response | None = None
        for response_format, output_budget in attempts:
            body: dict[str, Any] = {
                "model": self.settings.azure_openai_deployment,
                "instructions": instruction,
                "input": [{"role": "user", "content": content}],
                "max_output_tokens": output_budget,
                "store": False,
                "truncation": "disabled",
            }
            if response_format == "strict":
                body["text"] = {"format": {"type": "json_schema", "name": model.__name__, "strict": True, "schema": schema}}
            elif response_format == "json_object":
                body["text"] = {"format": {"type": "json_object"}}
            if self.settings.azure_reasoning_effort:
                body["reasoning"] = {"effort": self.settings.azure_reasoning_effort}
            for retry in range(2):
                try:
                    # Covers the complete HTTP transaction, not only socket inactivity.
                    async with asyncio.timeout(self.settings.llm_timeout_seconds):
                        response = await self.client.post(
                            self.settings.azure_openai_endpoint + "responses", json=body,
                            headers={"api-key": self.settings.azure_openai_api_key.get_secret_value()},
                        )
                except (httpx.TimeoutException, TimeoutError) as exc:
                    # Do not retry a timeout: Azure may already have processed the request.
                    raise AppError("azure_timeout", "Azure did not respond within the configured timeout. Try a smaller document or review the deployment capacity.", 504) from exc
                except httpx.RequestError as exc:
                    raise AppError("azure_connection", "The backend could not reach the Azure endpoint. Check networking and endpoint configuration.", 502) from exc
                if response.status_code in (429, 502, 503, 504) and retry == 0:
                    try:
                        delay = min(max(float(response.headers.get("retry-after", "1")), 0), 5)
                    except ValueError:
                        delay = 1
                    await asyncio.sleep(delay)
                    continue
                break
            if response.status_code != 400:
                self.response_format, self.output_budget = response_format, output_budget
                break
        assert response is not None
        if response.status_code == 400:
            fallback = await self._chat_fallback(instruction, text, image)
            if isinstance(fallback, tuple):
                return fallback
            response = fallback
        if response.status_code in (401, 403):
            raise AppError("azure_auth", "Azure rejected the credentials or access policy. Verify the resource key and deployment access.", 502)
        if response.status_code == 404:
            raise AppError("azure_deployment", "Azure could not find the deployment. AZURE_OPENAI_DEPLOYMENT must be the deployment name, and the endpoint must support /openai/v1/responses.", 502)
        if response.status_code == 429:
            raise AppError("azure_rate_limit", "Azure is rate-limiting this deployment. Retry after its quota is available.", 429)
        if response.is_error:
            return self._raise_provider_error(response)
        if len(response.content) > 4 * 1024 * 1024:
            raise AppError("azure_response_limit", "Azure returned an unexpectedly large response.", 502)
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError("azure_protocol", "Azure returned a non-JSON response.", 502) from exc
        if not isinstance(data, dict):
            raise AppError("azure_protocol", "Azure returned an invalid response envelope.", 502)
        usage_data = data.get("usage") or {}
        if not isinstance(usage_data, dict):
            raise AppError("azure_protocol", "Azure returned invalid usage metadata.", 502)
        try:
            usage = Usage(calls=1, input_tokens=usage_data.get("input_tokens", 0),
                          output_tokens=usage_data.get("output_tokens", 0))
        except ValidationError as exc:
            raise AppError("azure_protocol", "Azure returned invalid usage metadata.", 502) from exc
        if data.get("status") != "completed":
            raise AppError("azure_incomplete", "Azure did not complete the response. It may have reached its output budget or content filter. No partial plan was accepted.", 502)
        parts: list[str] = []
        output = data.get("output", [])
        if not isinstance(output, list):
            raise AppError("azure_protocol", "Azure returned an invalid output envelope.", 502)
        for item in output:
            if not isinstance(item, dict) or not isinstance(item.get("content", []), list):
                raise AppError("azure_protocol", "Azure returned an invalid output item.", 502)
            for part in item.get("content", []):
                if not isinstance(part, dict):
                    raise AppError("azure_protocol", "Azure returned invalid message content.", 502)
                if part.get("type") == "refusal":
                    raise AppError("azure_refusal", "Azure declined this document or request. Review the source content; no plan was generated.", 422)
                if part.get("type") == "output_text":
                    if not isinstance(part.get("text"), str):
                        raise AppError("azure_protocol", "Azure returned invalid output text.", 502)
                    parts.append(part["text"])
        try:
            parsed = json.loads("".join(parts))
        except ValueError:
            # The service can repair malformed structured output once.
            return {"_invalid_json_response": "".join(parts)[:20000]}, usage
        if not isinstance(parsed, dict):
            return {"_invalid_root": parsed}, usage
        return parsed, usage

    def _raise_provider_error(self, response: httpx.Response) -> None:
        diagnostic = provider_diagnostic(response)
        raise AppError("azure_request", f"Azure returned HTTP {response.status_code}.{diagnostic} The planner tried Responses and Chat Completions with structured and plain JSON at {self.settings.max_output_tokens} and 4096 output tokens. Check that the deployment supports vision for visual pages and that the deployment name is correct; Azure's provider message is not exposed.", 502)
