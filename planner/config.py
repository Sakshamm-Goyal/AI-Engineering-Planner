from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    # This must match the deployment name in YOUR Azure resource.
    azure_openai_deployment: str = "gpt-5.6-luna"
    azure_reasoning_effort: str = ""
    llm_timeout_seconds: float = Field(default=180, ge=5, le=600)
    # 8k is broadly supported by Azure Responses deployments. A larger value can
    # be configured after confirming the selected deployment's supported limit.
    max_output_tokens: int = Field(default=8192, ge=1024, le=64000)
    max_input_text_bytes: int = Field(default=350000, ge=10000, le=1000000)
    max_file_mb: int = Field(default=50, ge=1, le=100)
    max_pages: int = Field(default=500, ge=1, le=500)
    max_vision_pages: int = Field(default=500, ge=1, le=500)
    max_document_chars: int = Field(default=500000, ge=1000, le=500000)
    extraction_chunk_bytes: int = Field(default=60000, ge=10000, le=200000)
    extraction_chunk_concurrency: int = Field(default=3, ge=1, le=6)
    parser_timeout_seconds: float = Field(default=60, ge=1, le=120)
    pipeline_timeout_seconds: float = Field(default=1200, ge=10, le=1800)
    max_concurrent_runs: int = Field(default=2, ge=1, le=8)

    @field_validator("azure_openai_endpoint")
    @classmethod
    def normalize_endpoint(cls, value: str) -> str:
        if not value.strip():
            return ""
        parsed = urlsplit(value.strip())
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Use an HTTPS Azure resource endpoint without credentials or a query.")
        if parsed.path.rstrip("/") not in ("", "/openai/v1"):
            raise ValueError("Use the resource root or its /openai/v1/ endpoint.")
        return urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/", "", ""))

    @field_validator("azure_reasoning_effort")
    @classmethod
    def valid_effort(cls, value: str) -> str:
        if value not in ("", "none", "minimal", "low", "medium", "high", "xhigh"):
            raise ValueError("Unsupported reasoning effort setting.")
        return value

    @property
    def configured(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key.get_secret_value()
                    and self.azure_openai_deployment.strip())

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024
