# Security and data boundaries

I designed this version for a local, single-user process. I did not implement a public, multi-tenant service. Do not expose its unauthenticated endpoints to an untrusted network with a shared Azure key.

## What I implemented

I keep Azure credentials in backend settings, never browser JavaScript or exported plans. The health endpoint reports settings presence, not the key. Provider and generic error handling avoids returning source-bearing provider bodies; application error logs include exception class names rather than request contents.

I require upload consent, reject unsupported types and invalid PDF signatures, validate files through PDFium, reject encryption, and apply file/page/text/image budgets. Request bodies are capped before multipart parsing. Parsing occurs in a separate child process with cancellation/timeout cleanup; on Unix it also has CPU and address-space limits. The child is a robustness boundary, not a hardened sandbox. A PDF renderer remains native code with its own dependency risk.

I do not execute PDF scripts, follow document links, or give the LLM tools. Vision receives rendered page data rather than a remote URL supplied by the document. Instructions tell the model and coding agent to treat embedded text as data, not authority.

I validate structured output locally, check evidence and graph invariants, reject cycles and invalid references, bound repair attempts and selected HTTP retries, and disable automatic context truncation. I do not retry provider timeouts because the request may already have been processed and billed.

I escape model-derived strings before inserting them into the browser DOM. I do not render model Markdown as HTML. Responses include a restrictive same-origin Content Security Policy, no-sniff, frame restrictions, no-referrer, and no-store on API paths. Cross-origin browser mutations are rejected. These headers are tested at the API boundary; enforcement under real browser HTTP navigation was not exercised in this restricted delivery environment.

## Important limits

Cross-origin rejection is not authentication, and loopback binding is not a complete defense against a hostile machine, browser extension, or local process. Host/origin checks are not a comprehensive DNS-rebinding defense. Concurrency limits are per application process, not global quotas; increasing server workers changes aggregate capacity. Upload buffering is individually bounded but not a global memory admission-control system. Put authentication, trusted-host handling, request-rate/body/time limits, and quotas in front of any non-local deployment.

I revalidate the extraction snapshot the browser supplies, but do not sign it or associate it with a server-side immutable original. A caller can edit a self-consistent snapshot. That is compatible with local review, not authenticated provenance. A public workflow should bind the original document, review permissions, and generated plan to an authenticated owner.

The web application does not save a document history. Upload-parser temporary files are closed after use; current source data remains in browser memory and request processing while needed. Downloads and the explicit smoke-test artifact directory are retained files. I do not claim cryptographic secure erasure of memory or storage.

`store: false` is a Responses request setting, not a promise of zero provider-side data retention. Review the Azure resource's organizational policies, region, access controls, and service data-handling terms before processing confidential PRDs. I did not audit the user's tenant or Azure configuration.

Exported Markdown and prompts contain untrusted source content. Use a safe Markdown renderer and review agent instructions before execution. Do not treat an exported prompt as authorization to access secrets, modify unrelated code, spend money, or deploy to production.

## Before a public deployment

I would first define who may upload which documents and what retention is permitted, then add authenticated access, tenant isolation, explicit authorization, per-user limits, and secure operational logging. A durable processing queue would be justified only by the required lifecycle and traffic. TLS/proxy configuration must preserve correct origin handling and allow the documented long-running requests. I would also run dependency and container security checks in that deployment environment; this delivery does not claim a completed security audit.
