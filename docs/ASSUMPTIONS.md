# My assumptions and tradeoffs

## Application scope

I assumed a local, single-user engineering-planning workflow. I did not assume a public SaaS product, document collaboration, persistent history, or execution of generated code. A server restart can interrupt an in-flight operation. The browser does not persist workspace data across reloads. These choices remove a database, authentication implementation, and durable queue from the first version; they are not claims that those features would be unnecessary in a different deployment.

I interpreted “GPT Luna from Azure” as an Azure deployment supporting the Responses API, image input, and strict structured output. I made the resource endpoint, key, and deployment name configurable. I use API-key authentication in this version, not Microsoft Entra ID. I did not assume I had access to the user's resource, region, quota, credentials, or deployed alias.

I treat English, typed PRDs and readable printed scans as the initial evaluation scope. The app does not enforce a language detector, but I have not evaluated multilingual extraction or handwriting. The default limits are 20 MiB, 30 total pages, and 12 vision pages. A larger supported context window does not remove the need for cost, latency, and extraction-review boundaries.

## Why I simplified the proposal

I initially discussed React and PDF Inspector. I ultimately used plain TypeScript and a single PDFium dependency for extraction and rendering. The UI has one workspace and three states; a framework is not required to express them. The checked-in build can run without Node. PDF Inspector remains a useful reference, but adding a second engine or an OCR model runtime was not needed for the implemented boundary. I did not benchmark PDF Inspector against this implementation and do not claim it is worse.

I used a small HTTP adapter instead of adding an SDK plus an orchestration framework. The provider contract needed here is one Responses endpoint with structured output, selective image input, safe errors, and bounded retries. I isolated that contract in `planner/azure.py` so changing the deployment behavior does not spread through the application.

I did not add retrieval, a vector database, provider switching, a model-based reviewer, a separate LLM call for each task prompt, or a dependency graph editor. Each would add a component without addressing a demonstrated failure in the scoped workflow.

## Product requirements versus implementation decisions

The target application's stack is an input to the planner, not inherited from this application's implementation. A proposed stack or interface is labelled as a proposal. A source statement is not labelled “from the PRD” merely because it is plausible.

I include explicit non-functional constraints when they affect implementation, even though the brief primarily asks for functional requirements. Supporting work such as initialization or persistence may be necessary without being quoted verbatim in the PRD; it must map to the requirements it enables and have an implementation rationale.

I allow low-risk, reversible technical assumptions. I do not intentionally let assumptions resolve missing billing, security, permissions, or business-rule decisions. Those should become questions and block affected work. This distinction is instructed to the model but is not completely machine-verifiable.

A user can correct extracted descriptions, answer questions, and add implementation context. The original evidence remains visible and the correction is marked as user input. Corrected descriptions supersede conflicting original acceptance criteria for subsequent planning. The UI does not provide arbitrary insertion/deletion of requirement records or repository ingestion; omitted scope can be described in context, but substantial omissions are a reason to review or re-extract the document.

## What the validation means

I verify that evidence quotes occur in the retained page text after Unicode/whitespace normalization. That detects invented quotations against the extraction snapshot, not transcription errors in a scan. Page references are 1-based.

I require every extracted requirement to map to at least one task. Blocked requirements remain mapped to blocked tasks. I do not silently drop requirements into an excluded state during planning. Full mapping does not establish that extraction was complete or that the mapped task actually satisfies the requirement semantically.

“Ready” means no known blocking question and no task prerequisite. “Waiting” means prerequisites are listed. Neither means a repository has been inspected or a task has been executed. The stable topological order is graph-valid, not the only correct implementation order. Dependency waves are graph depth, not conflict-free agent scheduling.

S/M/L is a relative integration-and-uncertainty estimate, not an estimate in hours or an assurance about an agent's ability. I ask the model to split broad tasks where outcomes can be separated, not produce a fixed number of tasks.

## Remaining uncertainties

I have not measured real Luna requirement recall, unsupported-scope rate, scan transcription quality, latency, cost, or success when an agent executes the generated tasks. Those require the actual deployment and representative evaluation runs. The included smoke script captures real output once credentials are supplied; it does not substitute a model's own confidence for evaluation.

The routing heuristics can over-route a page with a logo or decorative vector content, and can under-detect an unruled table. That tradeoff is why I expose the page-reading method, warnings, original PDF, and force-vision control rather than claiming “best scan accuracy.”

## My final first-principles review

I kept the human review checkpoint because a missed or ambiguous business rule is more consequential than an extra click. I kept exact source references, deterministic graph validation, and bounded failure handling because they make errors visible. I removed the proposed framework and second PDF tool because this implementation did not need them.

I did not add another agent to approve the first agent, a dashboard without a user need, or a database solely to look production-like. Where the code was already appropriately scoped and tests passed, I left it alone. The remaining improvement priority is an evidence-based evaluation on the real Azure deployment, not additional infrastructure by default.
