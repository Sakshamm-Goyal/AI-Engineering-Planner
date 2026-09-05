"""Actual Chromium tests. External Azure responses are authored test fixtures."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from planner.config import ROOT

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1 to run Chromium tests.")]


@pytest.fixture(scope="module")
def server_factory():
    processes = []
    def start(module):
        if os.getenv("PLANNER_BROWSER_IN_MEMORY") == "1":
            return "http://fixture.local" if "fixture_server" in module else "http://sample.local"
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        env = dict(os.environ)
        # No real credentials enter the browser-test server.
        env.update(AZURE_OPENAI_API_KEY="", AZURE_OPENAI_ENDPOINT="")
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(proc)
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError("Browser-test server stopped during startup.")
            try:
                with urlopen(base + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        return base
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("Browser-test server did not start.")
    yield start
    for proc in processes:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture(scope="module")
def sample_server(server_factory):
    return server_factory("planner.app:app")


@pytest.fixture(scope="module")
def fixture_server(server_factory):
    return server_factory("tests.fixture_server:app")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
        browser = playwright.chromium.launch(executable_path=path, headless=True, args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1060}, device_scale_factor=1)
    page = context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    harness = None
    if os.getenv("PLANNER_BROWSER_IN_MEMORY") == "1":
        from tests.browser_harness import LocalPageHarness
        harness = LocalPageHarness(page)
    try:
        yield harness or page
        assert not errors, errors
    finally:
        if harness:
            harness.close_harness()
        context.close()


def screenshot(page, name):
    directory = os.getenv("PLANNER_SCREENSHOTS_DIR")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / name), full_page=False, animations="disabled")


def sample(page, server):
    page.goto(server)
    page.get_by_role("button", name="Explore sample plan").click()
    page.get_by_role("heading", name="Team Notes.", exact=True).wait_for()


def upload_to_review(page, server):
    page.goto(server)
    page.locator("#context").fill("I use FastAPI and SQLite in a new local repository.")
    page.locator("#pdf-file").set_input_files(ROOT / "examples/team-notes-prd.pdf")
    page.locator("#consent").check()
    page.get_by_role("button", name="Extract requirements", exact=True).click()
    page.get_by_role("heading", name="Make sure the spec is right.").wait_for()


def test_landing_has_working_sample_and_no_fake_connection(page, sample_server):
    from playwright.sync_api import expect
    page.goto(sample_server)
    expect(page.locator("#connection")).to_contain_text("Azure setup needed")
    expect(page.get_by_role("button", name="Extract requirements", exact=True)).to_be_disabled()
    screenshot(page, "01-upload-desktop.png")
    page.get_by_role("button", name="Explore sample plan").click()
    expect(page.get_by_role("heading", name="Team Notes.", exact=True)).to_be_visible()
    expect(page.locator(".sample-banner")).to_contain_text("No Azure inference was used")
    screenshot(page, "02-plan-desktop.png")


def test_sample_blockers_source_dialog_and_downloads(page, sample_server, tmp_path):
    from playwright.sync_api import expect
    sample(page, sample_server)
    page.locator('.task-item[data-id="T-008"]').click()
    expect(page.locator(".blocker-box")).to_contain_text("Q-001")
    page.locator(".agent-prompt summary").click()
    expect(page.locator("#agent-prompt-text")).to_have_value(__import__('re').compile("STOP — THIS TASK IS BLOCKED"))
    page.get_by_role("button", name="Source requirements", exact=True).click()
    page.locator('[data-action="source"]').first.click()
    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.locator(".source-dialog-body pre")).to_contain_text("Team Notes")
    page.get_by_role("button", name="Close source", exact=True).click()
    with page.expect_download() as download_info:
        page.get_by_role("button", name="JSON", exact=True).click()
    download = download_info.value
    target = tmp_path / download.suggested_filename
    download.save_as(target)
    data = json.loads(target.read_text())
    assert data["schema_version"] == "1.0" and len(data["plan"]["tasks"]) == 9
    with page.expect_download() as markdown_download:
        page.get_by_role("button", name="Export plan", exact=True).click()
    assert markdown_download.value.suggested_filename == "engineering-plan.md"


def test_full_upload_review_edit_plan_flow(page, fixture_server):
    from playwright.sync_api import expect
    upload_to_review(page, fixture_server)
    screenshot(page, "03-review-desktop.png")
    page.locator("#edit-R-001").fill("Use my explicitly revised maximum title length of 100 characters.")
    page.locator("#acknowledge").check()
    page.get_by_role("button", name="Generate implementation plan", exact=True).click()
    expect(page.get_by_role("heading", name="Team Notes.", exact=True)).to_be_visible()
    page.locator('.task-item[data-id="T-002"]').click()
    page.locator(".agent-prompt summary").click()
    expect(page.locator("#agent-prompt-text")).to_have_value(__import__('re').compile("100 characters"))
    page.get_by_role("button", name="Review inputs", exact=True).click()
    page.locator("#review-context").fill("I have changed the implementation context.")
    expect(page.locator('.step[data-screen="plan"]')).to_be_disabled()


def test_mobile_plan_has_no_horizontal_page_overflow(page, sample_server):
    from playwright.sync_api import expect
    page.set_viewport_size({"width": 390, "height": 844})
    sample(page, sample_server)
    expect(page.get_by_role("heading", name="Team Notes.", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    screenshot(page, "04-plan-mobile.png")
    page.locator('.task-item[data-id="T-008"]').click()
    expect(page.locator(".blocker-box")).to_contain_text("Q-001")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_untrusted_model_text_is_not_html(page, sample_server):
    data = json.loads((ROOT / "examples/sample-output.json").read_text())
    data["plan"]["project_title"] = '<img src=x onerror="window.XSS=1">'
    page.route("**/api/sample", lambda route: route.fulfill(json=data))
    sample_title = data["plan"]["project_title"] + "."
    page.goto(sample_server)
    page.get_by_role("button", name="Explore sample plan").click()
    page.get_by_role("heading", name=sample_title, exact=True).wait_for()
    assert page.evaluate("window.XSS") is None
    assert page.locator("h1 img").count() == 0


def test_malformed_upload_surfaces_error_not_a_fake_plan(page, fixture_server):
    from playwright.sync_api import expect
    page.goto(fixture_server)
    page.locator("#pdf-file").set_input_files(ROOT / "examples/malformed.pdf")
    page.locator("#consent").check()
    page.get_by_role("button", name="Extract requirements", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("malformed")
    expect(page.locator('.step[data-screen="plan"]')).to_be_disabled()


def test_clipboard_copies_the_selected_task(page, sample_server):
    from playwright.sync_api import expect
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=sample_server)
    sample(page, sample_server)
    page.get_by_role("button", name="Copy agent prompt", exact=True).click()
    expect(page.locator("#toast")).to_contain_text("Copied T-001")
    assert "T-001" in page.evaluate("navigator.clipboard.readText()")
