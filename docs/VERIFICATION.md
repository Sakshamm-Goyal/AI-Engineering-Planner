# What I verified

Verification scope: **the delivered build**. I distinguish application correctness tests from evidence about real model quality.

## Executed checks

| Check | Result | Boundary |
|---|---|---|
| Backend suite | **90 passed** | Real application, validators, fixtures, and PDF subprocesses; external Azure responses mocked. |
| Browser suite | **7 passed** | Real Chromium and real built frontend, using an in-memory FastAPI bridge because browser URL navigation is blocked here. |
| TypeScript | **Compiled successfully** | Strict mode, unused checks, no emitted source maps, locally served assets. |
| Local HTTP smoke | **Passed** | Actual Uvicorn subprocess and loopback HTTP: static assets, PDF upload, extraction, review, plan; authored Azure fixture provider. |
| PDF fixture inspection | **Passed visual review** | Rendered native, scanned, mixed, and table fixtures in PDFium; checked readable content and no clipped layout. |
| Azure live test | **Not executed** | No user resource credentials were supplied. |
| Docker image / hosted CI | **Not executed** | Configuration is included, not claimed as tested in this environment. |
| Coding-agent execution of exported tasks | **Not executed** | Generated prompts are not evidence of implemented code. |

I measured **94% statement coverage** for the in-process backend modules in this run. `planner/pdf_worker.py` is excluded from coverage instrumentation because the real parser runs in a child process. Its behavior is tested using actual PDF fixtures and subprocess execution; I do not claim the percentage covers its internal branches. Coverage is not a semantic-quality metric.

Machine-readable records: [`backend-tests.xml`](backend-tests.xml), [`browser-tests.xml`](browser-tests.xml), [`coverage.json`](coverage.json), and [`local-http-smoke.json`](local-http-smoke.json). Screenshots are under [`screenshots/`](screenshots/).

## What the backend suite checks

I test PDF routing for native, scanned, mixed, and table-containing documents; encrypted and malformed rejection; page, vision-page, text, and input bounds; and parser timeout/cancellation cleanup. I test source-quote matching, requirement references, corrections, questions, graph cycles, stable ordering, dependency depth, requirement mapping, and blocker propagation.

The HTTP-provider tests inspect the outgoing Azure endpoint, deployment field, strict schema, image data, `store: false`, and disabled truncation. I exercise error redaction, authentication failures, rate limits, bounded transient retries, non-retried timeouts, refusals, incomplete responses, malformed JSON, invalid response envelopes, text budgets, and repair limits using `httpx.MockTransport`. This establishes the implemented contract, not compatibility with an unqueried tenant or deployed model.

The API tests include consent, file types, body limits, origin checks, security headers, missing fields, configuration state, sample/live separation, resource-slot release, and a full native-PDF workflow with the actual Azure adapter and a mocked external transport.

## What the browser checks establish

I load the actual compiled TypeScript and CSS in Chromium. The tests cover the landing page, honest configuration status, sample banner, blocked-task stop prompts, source dialog, export payloads, requirement corrections, plan regeneration state, malformed-input errors, escaping of untrusted model text, prompt copy payload, and a 390-pixel mobile layout without horizontal page overflow.

I caught and corrected two concrete UI issues: editing review inputs could leave an old plan available, and the asynchronous startup configuration check could replace a file input during selection. I also reviewed screenshots and improved muted-text contrast.

The in-memory test harness replaces network fetch with an actual FastAPI TestClient bridge. It captures download payloads and stubs clipboard access. It does **not** test browser HTTP navigation, enforcement of the response CSP, native PDF-viewer behavior, operating-system download dialogs, or actual clipboard permissions. Separately, the Uvicorn smoke check verified real HTTP transport and header presence. Normal HTTP Playwright tests are included for execution outside this restricted environment.

## Reference outputs are not live outputs

The Team Notes sample is manually authored: 8 requirements, 9 tasks, one unresolved search decision, and two tasks blocked after propagation. Those counts describe the fixture, not a mandated result for real inference. The fixture mode is explicit and its recorded Azure usage is zero.

For a real deployment evaluation, I would review requirement omissions and invented scope separately. I would verify numerical limits, exclusions, archive/404 behavior, persistence, the unresolved multiword-search rule, scan transcription, and whether task criteria align with any user corrections. I would also inspect interface consistency and task granularity rather than accepting a graph-valid plan as sufficient.

Run `python scripts/smoke_azure.py --allow-billable-calls` to capture genuine inference results after configuring the resource. I included both a native and a scanned fixture so the visual path can be exercised deliberately. The command performs billable processing and retains source-bearing artifacts; review them before handing tasks to an agent.
