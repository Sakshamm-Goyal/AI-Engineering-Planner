"""Browser-test server only. NEVER use this module as the real application."""
from planner.app import create_app
from planner.config import Settings
from tests.conftest import FixtureProvider

app = create_app(Settings(_env_file=None,
    azure_openai_endpoint="https://fixture.openai.azure.com",
    azure_openai_api_key="fixture-not-a-real-key",
    azure_openai_deployment="test-fixture-not-live"), provider=FixtureProvider())
