import type {Extraction, Health, Requirement, Result, Review, Task} from './types.js';

type Screen = 'upload' | 'review' | 'plan';
type Tab = 'tasks' | 'requirements' | 'implementation' | 'questions';
const root = document.querySelector<HTMLDivElement>('#root')!;
const sourceDialog = document.querySelector<HTMLDialogElement>('#source-dialog')!;
const state = {
  screen: 'upload' as Screen, tab: 'tasks' as Tab, health: null as Health | null,
  file: null as File | null, fileUrl: '', context: '', consent: false, visionMode: 'auto',
  extraction: null as Extraction | null, result: null as Result | null,
  corrections: {} as Record<string, string>, answers: {} as Record<string, string>,
  acknowledged: false, selectedTask: '', error: '', busy: '', sample: false,
};

const icons: Record<string, string> = {
  upload: '<path d="M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4"/>',
  document: '<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4"/>',
  link: '<path d="m9 15 6-6m-8 4-2 2a4 4 0 0 0 6 6l3-3m-4-12 3-3a4 4 0 0 1 6 6l-2 2"/>',
  alert: '<path d="m12 3 10 18H2zM12 9v5m0 3v1"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8zm-10 10 10 5 10-5m-20 5 10 5 10-5"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>',
};
const icon = (name: string) => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.document}</svg>`;
const esc = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]!));
const list = (items: string[], className = 'text-list') => `<ul class="${className}">${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
const statusLabel = (task: Task) => ({ready: 'Ready to start', waiting: 'Waiting on dependencies', blocked: 'Needs clarification'}[task.readiness]);
const sampleBanner = () => state.sample ? '<div class="sample-banner"><span class="pill sample-pill">SAMPLE</span><span>This is an illustrative, hand-authored plan. No Azure inference was used.</span><button class="text-button" data-action="new">Use my own PRD '+icon('arrow')+'</button></div>' : '';
const originalLink = () => state.fileUrl || (state.sample ? '/api/sample/pdf' : '');

function render(): void {
  document.querySelector('#breadcrumb')!.textContent = state.screen === 'upload' ? 'NEW PLAN' : (state.extraction?.document.title || 'NEW PLAN').toUpperCase();
  const active = ['upload', 'review', 'plan'].indexOf(state.screen);
  document.querySelector('#steps')!.innerHTML = [
    ['upload', 'Upload a PRD', 'Start with the source'],
    ['review', 'Review requirements', 'Resolve the unknowns'],
    ['plan', 'Implementation plan', 'Hand off with confidence'],
  ].map(([screen, title, subtitle], i) => {
    const enabled = !state.busy && (i === 0 || (i === 1 && state.extraction) || (i === 2 && state.result));
    return `<button class="step ${active === i ? 'active' : ''} ${active > i ? 'complete' : ''}" data-action="navigate" data-screen="${screen}" ${enabled ? '' : 'disabled'} ${active === i ? 'aria-current="step"' : ''}><span class="step-number">${active > i ? icon('check') : `0${i + 1}`}</span><span><strong>${title}</strong><small>${subtitle}</small></span></button>`;
  }).join('');
  document.querySelector<HTMLButtonElement>('.new-plan')!.disabled = Boolean(state.busy);
  const health = state.health;
  document.querySelector('#connection')!.innerHTML = health
    ? `<span class="connection-dot ${health.azure_configured ? 'configured' : ''}"></span>${health.azure_configured ? 'Azure configured' : 'Azure setup needed'}<span class="deployment-name">${esc(health.deployment)}</span>`
    : '<span class="connection-dot"></span>Backend unavailable';
  const error = state.error ? `<div class="error-banner" role="alert">${icon('alert')}<div><strong>Something needs attention</strong><p>${esc(state.error)}</p></div><button class="icon-button" data-action="dismiss-error" aria-label="Dismiss error">${icon('close')}</button></div>` : '';
  root.setAttribute('aria-busy', String(Boolean(state.busy)));
  root.innerHTML = error + (state.busy ? busyView() : state.screen === 'upload' ? uploadView() : state.screen === 'review' ? reviewView() : planView());
}

function uploadView(): string {
  const maxMb = state.health?.max_file_mb ?? 20;
  return `<section class="intro"><div class="eyebrow"><span></span> SPEC-TO-BUILD WORKSPACE</div><h1>A clear plan.<br><span>Before the first line.</span></h1><p>Turn a product requirements document into ordered engineering<br class="desktop-br"> tasks, grounded in the source and ready for your coding agent.</p></section>
  <div class="upload-layout"><section class="card upload-card"><div class="card-heading"><div><span class="section-number">01 / THE INPUT</span><h2>Start with your PRD</h2></div><span class="pill">PDF</span></div>
    <input type="file" id="pdf-file" accept="application/pdf,.pdf" hidden>
    <button class="dropzone ${state.file ? 'has-file' : ''}" id="dropzone" data-action="choose"><span class="upload-symbol">${icon(state.file ? 'document' : 'upload')}</span><strong>${state.file ? esc(state.file.name) : 'Drop your document here'}</strong><span>${state.file ? `${(state.file.size / 1024 / 1024).toFixed(2)} MB · Click to replace` : 'or click to choose a PDF'}</span><small>Text, scanned or mixed · Up to ${maxMb} MB</small></button>
    <div class="form-field"><label>Document reading <span class="optional">AUTOMATIC</span></label><p class="field-help">Native PDF text is used first; only pages without text are read with local OCR. Up to ${state.health?.max_pages ?? 500} pages.</p></div>
    <label class="checkbox-row" for="consent"><input id="consent" type="checkbox" ${state.consent ? 'checked' : ''}><span>I confirm this PRD may be sent to AI for processing.</span></label>
    <button class="button primary full" id="extract-button" data-action="extract" ${!state.file || !state.consent || !state.health?.azure_configured ? 'disabled' : ''}>Extract requirements ${icon('arrow')}</button>
    ${!state.health?.azure_configured ? '<p class="setup-hint">Add your Azure credentials to <code>.env</code> and restart the backend. You can explore the sample now.</p>' : '<p class="quiet-center">You can review and correct the requirements before planning.</p>'}
  </section>
  <div class="upload-right"><section class="card context-card"><span class="section-number">02 / YOUR CONTEXT</span><h2>A little direction helps.</h2><p>Tell the planner what it should work with—not what it should guess.</p><label for="context">Implementation context <span class="optional">OPTIONAL</span></label><textarea id="context" rows="5" maxlength="12000" placeholder="For example: I am adding this to an existing FastAPI and React app. Keep PostgreSQL, reuse our auth, and avoid new services.">${esc(state.context)}</textarea><div class="context-note">${icon('layers')}<span>No preferred stack? The planner will label its technical choices as proposals.</span></div></section>
  <section class="sample-card"><div class="sample-icon">${icon('document')}</div><div><span class="section-number">TAKE A LOOK FIRST</span><h3>A small PRD. A complete example.</h3><p>Explore Team Notes: requirements, dependencies and copy-ready agent prompts.</p><button class="text-button" data-action="sample">Explore sample plan ${icon('arrow')}</button></div></section></div></div>
  <div class="principles"><div><span>01</span><strong>Grounded in the source</strong><p>Page references, not invented scope.</p></div><div><span>02</span><strong>Ordered by dependency</strong><p>A checked graph, not a numbered guess.</p></div><div><span>03</span><strong>Ready for handoff</strong><p>Clear boundaries and testable outcomes.</p></div></div>`;
}

function busyView(): string {
  return `<section class="processing-view" role="status"><div class="processing-icon">${icon('layers')}</div><span class="eyebrow">WORK IN PROGRESS</span><h1>${esc(state.busy)}</h1><p>${state.screen === 'upload' ? 'Reading the document, checking visual pages and extracting source-linked requirements.' : 'Building cohesive tasks, checking the dependency graph and assembling coding-agent prompts.'}</p><div class="processing-track"><span></span></div><small>Keep this tab open. A failed or incomplete response will not be presented as a finished plan.</small></section>`;
}

function requirementCard(req: Requirement, editable: boolean): string {
  const correction = state.corrections[req.id];
  return `<article class="requirement-card"><div class="requirement-heading"><span class="mono-label">${esc(req.id)}</span><span class="pill subtle">${esc(req.kind.replaceAll('_', ' '))}</span></div><h3>${esc(req.title)}</h3>
    ${editable ? `<label class="sr-only" for="edit-${req.id}">Description for ${esc(req.id)}</label><textarea id="edit-${req.id}" class="requirement-edit" data-requirement="${esc(req.id)}" rows="3" maxlength="6000">${esc(correction ?? req.description)}</textarea>` : `<p>${esc(correction ?? req.description)}</p>`}
    <div class="evidence-row"><span>${icon('link')} SOURCE</span>${req.evidence.map(e => `<button class="source-pill" data-action="source" data-page="${e.page}">Page ${e.page} ${icon('chevron')}</button>`).join('')}</div>
    <details class="evidence-details"><summary>Source quotation${req.evidence.length > 1 ? 's' : ''}</summary>${req.evidence.map(e => `<blockquote>${esc(e.quote)}<cite>Page ${e.page}</cite></blockquote>`).join('')}${list(req.acceptance_criteria, 'criteria-list')}</details>
    ${correction && correction !== req.description ? '<small class="edit-label">User correction · original source evidence retained</small>' : ''}</article>`;
}

function reviewView(): string {
  const extraction = state.extraction!;
  const editable = !state.sample;
  const vision = extraction.pages.filter(p => p.method === 'vision').length;
  return `${sampleBanner()}<section class="page-heading"><div><span class="eyebrow">02 / REVIEW BEFORE YOU BUILD</span><h1>Make sure the spec is right.</h1><p>${esc(extraction.document.title)} · ${extraction.pages.length} pages · ${extraction.document.requirements.length} requirements</p></div>${originalLink() ? `<a class="button secondary" href="${esc(originalLink())}" target="_blank" rel="noopener noreferrer">${icon('document')} Original PDF</a>` : ''}</section>
    <div class="review-layout"><section><div class="section-heading"><h2>Extracted requirements</h2><span class="muted">${editable ? 'Descriptions are editable' : 'Read-only sample'}</span></div><p class="section-help">${editable ? 'Corrections stay separate from the original evidence. Acceptance criteria are planning inputs—not proof of implementation.' : 'This fixture shows what an extracted, reviewed specification looks like.'}</p>${extraction.document.requirements.map(r => requirementCard(r, editable)).join('')}</section>
    <aside class="review-aside"><section class="card reading-card"><div class="section-heading"><h3>Reading notes</h3><span class="pill">${vision ? 'VISION + TEXT' : 'NATIVE TEXT'}</span></div><div class="reading-stats"><strong>${extraction.pages.length - vision}<small>native pages</small></strong><strong>${vision}<small>vision pages</small></strong></div>${list(extraction.warnings)}<details><summary>Inspect page text</summary><div class="page-links">${extraction.pages.map(p => `<button class="source-pill" data-action="source" data-page="${p.number}">Page ${p.number} · ${p.method}</button>`).join('')}</div></details></section>
    <section class="card questions-card"><span class="section-number">DECISIONS, NOT GUESSES</span><h3>Open questions <span class="count">${extraction.document.questions.length}</span></h3>${extraction.document.questions.length ? extraction.document.questions.map(q => `<div class="review-question"><div class="question-meta"><span class="mono-label">${esc(q.id)}</span><span class="pill ${q.blocking ? 'warning' : 'subtle'}">${q.blocking ? 'BLOCKING' : 'OPTIONAL'}</span></div><label for="answer-${q.id}">${esc(q.question)}</label><p>${esc(q.why_it_matters)}</p><textarea id="answer-${q.id}" data-question="${esc(q.id)}" rows="3" maxlength="6000" placeholder="Your clarification…" ${editable ? '' : 'disabled'}>${esc(state.answers[q.id] || '')}</textarea></div>`).join('') : '<p>No explicit questions were extracted. Review the source for anything the model may have missed.</p>'}</section>
    <section class="card"><label for="review-context">Implementation context</label><textarea id="review-context" rows="5" maxlength="12000" ${editable ? '' : 'disabled'}>${esc(state.context)}</textarea><p class="field-help">Unanswered blocking questions remain visible and block affected tasks.</p>${editable ? `<label class="checkbox-row" for="acknowledge"><input id="acknowledge" type="checkbox" ${state.acknowledged ? 'checked' : ''}><span>I reviewed the reading notes and checked the source evidence.</span></label><button class="button primary full" data-action="generate" id="generate-button" ${state.acknowledged ? '' : 'disabled'}>Generate implementation plan ${icon('arrow')}</button>` : '<button class="button primary full" data-action="navigate" data-screen="plan">View sample tasks '+icon('arrow')+'</button>'}</section></aside></div>`;
}

function planView(): string {
  const result = state.result!;
  const plan = result.plan;
  const blocked = plan.tasks.filter(t => t.readiness === 'blocked').length;
  return `${sampleBanner()}<section class="page-heading plan-heading"><div><span class="eyebrow">03 / THE IMPLEMENTATION PLAN</span><h1>${esc(plan.project_title)}<span class="title-period">.</span></h1><p>${esc(plan.summary)}</p></div><div class="export-actions"><button class="button secondary compact" data-action="export-json">JSON ${icon('download')}</button><button class="button primary compact" data-action="export-md">Export plan ${icon('download')}</button></div></section>
    <div class="plan-stats"><div><strong>${plan.tasks.length.toString().padStart(2, '0')}</strong><span>engineering tasks</span></div><div><strong>${result.extraction.document.requirements.length.toString().padStart(2, '0')}</strong><span>requirements mapped</span></div><div><strong>${Math.max(...plan.tasks.map(t => t.wave)).toString().padStart(2, '0')}</strong><span>dependency waves</span></div><div class="${blocked ? 'has-blockers' : ''}"><strong>${blocked.toString().padStart(2, '0')}</strong><span>tasks need clarification</span></div><div class="stats-note">${icon('check')}<span>Dependency graph checked<br><small>Mapping is not an extraction-accuracy score.</small></span></div></div>
    <nav class="plan-tabs" aria-label="Plan sections">${(['tasks', 'requirements', 'implementation', 'questions'] as Tab[]).map(tab => `<button data-action="tab" data-tab="${tab}" class="${state.tab === tab ? 'active' : ''}" aria-current="${state.tab === tab ? 'page' : 'false'}">${{tasks: 'Engineering tasks', requirements: 'Source requirements', implementation: 'Implementation context', questions: 'Open questions'}[tab]}${tab === 'questions' && plan.open_questions.length ? `<span class="count">${plan.open_questions.length}</span>` : ''}</button>`).join('')}<button class="prompts-export" data-action="export-prompts">${icon('code')} Export prompts</button></nav>
    ${state.tab === 'tasks' ? taskWorkspace() : state.tab === 'requirements' ? sourceRequirements() : state.tab === 'implementation' ? implementationView() : questionsView()}
    <div class="plan-footnote"><span>${result.mode === 'sample' ? 'Illustrative output · no model calls' : `${result.usage.calls} successful model responses · ${(result.usage.input_tokens + result.usage.output_tokens).toLocaleString()} reported tokens`}</span><button class="text-button" data-action="navigate" data-screen="review">Review inputs ${icon('arrow')}</button></div>`;
}

function taskWorkspace(): string {
  const tasks = state.result!.plan.tasks;
  const task = tasks.find(t => t.id === state.selectedTask) || tasks[0];
  state.selectedTask = task.id;
  return `<section class="task-workspace"><aside class="task-list-panel"><div class="task-list-heading"><span class="section-number">EXECUTION ORDER</span><span>${tasks.length} TASKS</span></div><label class="sr-only" for="task-search">Filter tasks</label><input id="task-search" class="task-search" placeholder="Filter tasks by title or ID…" type="search"><div class="task-list">${tasks.map(t => `<button class="task-item ${t.id === task.id ? 'selected' : ''}" data-action="task" data-id="${esc(t.id)}" data-search="${esc((t.id + ' ' + t.title).toLowerCase())}" aria-pressed="${t.id === task.id}"><span class="task-order">${t.order.toString().padStart(2, '0')}</span><span class="task-item-content"><span class="task-item-meta"><span>${esc(t.id)}</span><span class="complexity">${t.complexity}</span></span><strong>${esc(t.title)}</strong><span class="task-status ${t.readiness}"><i></i>${statusLabel(t)}</span></span></button>`).join('')}</div><p class="task-list-note">Waves group tasks by dependency depth. They do not estimate duration or guarantee conflict-free parallel work.</p></aside>
    <article class="task-detail" aria-label="Selected task"><div class="detail-topline"><span class="mono-label">${esc(task.id)} <span>/</span> WAVE ${task.wave}</span><button class="button secondary compact" data-action="copy-prompt">${icon('copy')} Copy agent prompt</button></div><h2>${esc(task.title)}</h2><p class="task-description">${esc(task.description)}</p><div class="task-badges"><span class="pill ${task.readiness === 'blocked' ? 'warning' : 'green'}">${statusLabel(task)}</span><span class="pill">COMPLEXITY ${task.complexity}</span><span class="pill subtle">${esc(task.release_target)}</span><span class="pill subtle">${esc(task.priority.toUpperCase())}</span>${task.requirement_ids.map(id => `<span class="pill subtle">${esc(id)}</span>`).join('')}</div>
    ${task.blocked_by.length ? `<div class="blocker-box">${icon('alert')}<div><strong>Resolve before implementation</strong>${list(task.blocked_by.map(id => `${id}: ${state.result!.plan.open_questions.find(q => q.id === id)?.question || id}`))}</div></div>` : ''}
    <div class="dependency-strip"><span>DEPENDS ON</span>${task.dependencies.length ? task.dependencies.map(id => `<button class="source-pill" data-action="task" data-id="${esc(id)}">${esc(id)} ${icon('chevron')}</button>`).join('') : '<p>No prerequisite tasks</p>'}</div>
    <section class="detail-section"><h3>${icon('check')} Acceptance criteria</h3>${list(task.acceptance_criteria, 'criteria-list')}</section><section class="detail-section"><h3>${icon('layers')} Implementation outline</h3><ol class="outline-list">${task.implementation_steps.map(step => `<li>${esc(step)}</li>`).join('')}</ol></section>
    <div class="detail-columns"><section class="detail-section"><h3>Test scenarios</h3>${list(task.test_cases)}</section><section class="detail-section"><h3>Scope boundaries</h3>${list(task.out_of_scope)}<p class="complexity-reason"><strong>Why ${task.complexity}?</strong> ${esc(task.complexity_reason)}</p></section></div>
    <details class="agent-prompt"><summary>${icon('code')} Coding-agent prompt <span>VIEW FULL PROMPT</span></summary><p>The prompt includes context, source requirements, prerequisites and a completion contract. It does not assume code has already been written.</p><label class="sr-only" for="agent-prompt-text">Full coding-agent prompt</label><textarea id="agent-prompt-text" readonly rows="16">${esc(task.agent_prompt)}</textarea><button class="button secondary compact" data-action="copy-prompt">${icon('copy')} Copy prompt</button></details></article></section>`;
}

function sourceRequirements(): string {
  const result = state.result!;
  return `<div class="source-plan-layout"><section>${result.extraction.document.requirements.map(r => requirementCard(r, false)).join('')}</section><aside class="card mapping-card"><span class="section-number">TRACEABILITY</span><h3>Requirement → task</h3><p>All extracted requirements are accounted for. Source completeness still needs human review.</p>${result.plan.coverage.map(row => `<div class="mapping-row"><strong>${esc(row.requirement_id)}</strong><span>${row.task_ids.map(id => `<button class="source-pill" data-action="task" data-id="${esc(id)}">${esc(id)}</button>`).join('')}</span></div>`).join('')}</aside></div>`;
}

function implementationView(): string {
  const plan = state.result!.plan;
  return `<div class="implementation-layout"><section class="card"><span class="section-number">SHARED TECHNICAL CONTEXT</span><h2>One consistent implementation.</h2><p>${esc(plan.technical_context.approach)}</p><div class="stack-pills">${plan.technical_context.stack.map(s => `<span class="pill green">${esc(s)}</span>`).join('')}</div><h3>Proposed interfaces and contracts</h3>${list(plan.technical_context.interfaces)}<h3>Decisions and their basis</h3>${plan.technical_context.decisions.map(d => `<div class="decision"><span class="pill ${d.basis === 'proposed' ? 'warning' : 'subtle'}">${esc(d.basis.replaceAll('_', ' '))}</span><h4>${esc(d.decision)}</h4><p>${esc(d.rationale)}</p></div>`).join('')}</section><aside><section class="card"><span class="section-number">EXPLICIT, NOT HIDDEN</span><h3>Assumptions</h3>${list(plan.assumptions)}</section><section class="card"><h3>Excluded scope</h3>${list(state.extraction!.document.out_of_scope)}</section></aside></div>`;
}

function questionsView(): string {
  const questions = state.result!.plan.open_questions;
  return `<section class="questions-plan"><div class="section-heading"><div><h2>Decisions that need a person.</h2><p>Blocking questions propagate to dependent tasks. To clarify new questions, return to review and add their answers to implementation context.</p></div></div>${questions.length ? questions.map(q => `<article class="card question-plan"><div><span class="mono-label">${esc(q.id)}</span><span class="pill ${q.blocking ? 'warning' : 'subtle'}">${q.blocking ? 'BLOCKING' : 'NON-BLOCKING'}</span></div><h3>${esc(q.question)}</h3><p>${esc(q.why_it_matters)}</p><div class="evidence-row">Affects ${q.requirement_ids.length ? q.requirement_ids.map(id => `<span class="pill">${esc(id)}</span>`).join('') : 'the whole project'}</div></article>`).join('') : '<div class="card"><h3>No known open questions</h3><p>This does not guarantee the PRD is unambiguous. Review the assumptions and source requirements before execution.</p></div>'}</section>`;
}

async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), (url === "/api/health" ? 15 : (state.health?.pipeline_timeout_seconds ?? 600) + 15) * 1000);
  try {
    const response = await fetch(url, {...init, signal: controller.signal});
    let data;
    try { data = await response.json(); } catch { throw new Error(`The backend returned a non-JSON response (HTTP ${response.status}). Check the server and proxy settings.`); }
    if (!response.ok) {
      const details = data.error?.details?.map((d: {field: string; message: string}) => `${d.field}: ${d.message}`).join('; ');
      throw new Error((data.error?.message || `Request failed (HTTP ${response.status}).`) + (details ? ` ${details}` : ''));
    }
    return data as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new Error('The browser stopped waiting for this request. Server work may still be finishing; avoid immediately submitting duplicate runs.');
    throw error;
  } finally { window.clearTimeout(timeout); }
}

async function run(label: string, action: () => Promise<void>): Promise<void> {
  if (state.busy) return;
  state.busy = label; state.error = ''; render();
  try { await action(); } catch (error) { state.error = error instanceof Error ? error.message : 'An unexpected error occurred.'; }
  finally { state.busy = ''; render(); window.scrollTo({top: 0, behavior: 'instant'}); }
}

function collectReview(): Review {
  const original = new Map(state.extraction!.document.requirements.map(r => [r.id, r.description]));
  return {implementation_context: state.context,
    corrections: Object.entries(state.corrections).filter(([id, text]) => text.trim() !== original.get(id)).map(([requirement_id, description]) => ({requirement_id, description})),
    answers: Object.entries(state.answers).filter(([, answer]) => answer.trim()).map(([question_id, answer]) => ({question_id, answer})),
    acknowledge_warnings: state.acknowledged};
}

function selectFile(file: File): void {
  const limit = (state.health?.max_file_mb ?? 20) * 1024 * 1024;
  if (!file.name.toLowerCase().endsWith('.pdf')) { state.error = 'Choose a PDF document. Images and renamed text files are not supported.'; render(); return; }
  if (!file.size || file.size > limit) { state.error = `Choose a non-empty PDF no larger than ${state.health?.max_file_mb ?? 20} MB.`; render(); return; }
  if (state.fileUrl) URL.revokeObjectURL(state.fileUrl);
  state.file = file; state.fileUrl = URL.createObjectURL(file); state.extraction = null; state.result = null;
  state.sample = false; state.acknowledged = false; state.corrections = {}; state.answers = {}; state.error = ''; render();
}

function showSource(number: number): void {
  const page = state.extraction?.pages.find(p => p.number === number);
  if (!page) return;
  document.querySelector('#source-content')!.innerHTML = `<div class="source-dialog-header"><div><span class="eyebrow">SOURCE EVIDENCE</span><h2 id="source-title">Page ${number.toString().padStart(2, '0')}</h2></div><button class="icon-button" data-action="close-source" aria-label="Close source">${icon('close')}</button></div><div class="source-dialog-body"><span class="pill ${page.method === 'vision' ? 'warning' : 'green'}">${page.method === 'vision' ? 'VISION TRANSCRIPTION · VERIFY AGAINST PDF' : 'NATIVE TEXT EXTRACTION'}</span>${page.warnings.length ? list(page.warnings) : ''}<pre>${esc(page.text)}</pre>${page.method === 'vision' && page.native_text ? `<details><summary>Compare native extraction</summary><pre>${esc(page.native_text)}</pre></details>` : ''}${originalLink() ? `<a class="button secondary" href="${esc(originalLink())}#page=${number}" target="_blank" rel="noopener noreferrer">Open original PDF ${icon('arrow')}</a>` : ''}</div>`;
  sourceDialog.showModal();
}

let toastTimer = 0;
function toast(message: string): void {
  const element = document.querySelector<HTMLDivElement>('#toast')!;
  window.clearTimeout(toastTimer); element.textContent = message; element.hidden = false;
  toastTimer = window.setTimeout(() => { element.hidden = true; }, 4000);
}
function download(text: string, filename: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], {type: mime}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`Downloaded ${filename}`);
}

async function handleAction(action: string, element: HTMLElement): Promise<void> {
  if (state.busy) return;
  switch (action) {
    case 'choose': document.querySelector<HTMLInputElement>('#pdf-file')?.click(); break;
    case 'new':
      if ((state.extraction || state.file) && !window.confirm('Start a new plan? The current workspace is not saved. Export anything you need first.')) return;
      if (state.fileUrl) URL.revokeObjectURL(state.fileUrl);
      Object.assign(state, {screen: 'upload', file: null, fileUrl: '', context: '', consent: false, extraction: null, result: null, sample: false, corrections: {}, answers: {}, acknowledged: false, selectedTask: '', error: '', tab: 'tasks'});
      render(); window.scrollTo(0, 0); break;
    case 'dismiss-error': state.error = ''; render(); break;
    case 'navigate': {
      const screen = element.dataset.screen as Screen;
      if (screen === 'review' && !state.extraction || screen === 'plan' && !state.result) return;
      state.screen = screen; state.error = ''; render(); window.scrollTo(0, 0); break;
    }
    case 'sample':
      await run('Opening the sample plan…', async () => {
        const result = await api<Result>('/api/sample');
        if (state.fileUrl) URL.revokeObjectURL(state.fileUrl);
        Object.assign(state, {result, extraction: result.extraction, sample: true, context: result.review.implementation_context,
          corrections: {}, answers: {}, acknowledged: true, selectedTask: result.plan.tasks[0].id, screen: 'plan', tab: 'tasks', file: null, fileUrl: ''});
      }); break;
    case 'extract':
      if (!state.file || !state.consent) return;
      await run('Understanding your requirements…', async () => {
        const body = new FormData(); body.append('file', state.file!); body.append('consent', 'true');
        body.append('implementation_context', state.context); body.append('vision_mode', state.visionMode);
        state.extraction = await api<Extraction>('/api/extract', {method: 'POST', body});
        state.result = null; state.sample = false; state.screen = 'review'; state.corrections = {}; state.answers = {}; state.acknowledged = false;
      }); break;
    case 'generate':
      if (!state.extraction || !state.acknowledged || state.sample) return;
      if (Object.values(state.corrections).some(v => !v.trim())) { state.error = 'A requirement description cannot be empty. Correct it rather than deleting its scope.'; render(); return; }
      await run('Turning the spec into a build plan…', async () => {
        state.result = await api<Result>('/api/plan', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({extraction: state.extraction, review: collectReview()})});
        state.screen = 'plan'; state.tab = 'tasks'; state.selectedTask = state.result.plan.tasks[0].id;
      }); break;
    case 'tab': state.tab = element.dataset.tab as Tab; render(); break;
    case 'task': state.selectedTask = element.dataset.id!; state.tab = 'tasks'; render(); break;
    case 'source': showSource(Number(element.dataset.page)); break;
    case 'close-source': sourceDialog.close(); break;
    case 'copy-prompt': {
      const task = state.result?.plan.tasks.find(t => t.id === state.selectedTask);
      if (!task) return;
      try { await navigator.clipboard.writeText(task.agent_prompt); toast(`Copied ${task.id} agent prompt`); }
      catch { toast('Clipboard access was blocked. Expand the prompt and copy its text manually.'); }
      break;
    }
    case 'export-json': if (state.result) download(JSON.stringify(state.result, null, 2), 'engineering-plan.json', 'application/json'); break;
    case 'export-md': if (state.result) download(state.result.markdown, 'engineering-plan.md', 'text/markdown;charset=utf-8'); break;
    case 'export-prompts': if (state.result) download(`# Coding-agent prompts\n\nMode: ${state.result.mode}\n\n` + state.result.plan.tasks.map(t => `## ${t.order}. ${t.id} — ${t.title}\n\n${t.agent_prompt}\n`).join('\n---\n\n'), 'agent-prompts.md', 'text/markdown;charset=utf-8'); break;
  }
}

document.addEventListener('click', event => {
  const element = (event.target as Element).closest<HTMLElement>('[data-action]');
  if (element && !element.hasAttribute('disabled')) void handleAction(element.dataset.action!, element);
});
document.addEventListener('input', event => {
  const input = event.target as HTMLInputElement | HTMLTextAreaElement;
  if (input.id === 'context' || input.id === 'review-context') state.context = input.value;
  if (!state.sample && state.result && (input.id === 'review-context' || input.dataset.requirement || input.dataset.question)) {
    state.result = null;
    document.querySelector<HTMLButtonElement>('.step[data-screen="plan"]')?.setAttribute('disabled', '');
  }
  if (input.dataset.requirement) state.corrections[input.dataset.requirement] = input.value;
  if (input.dataset.question) state.answers[input.dataset.question] = input.value;
  if (input.id === 'task-search') {
    const query = input.value.trim().toLowerCase();
    document.querySelectorAll<HTMLElement>('.task-item').forEach(item => { item.hidden = !item.dataset.search?.includes(query); });
  }
});
document.addEventListener('change', event => {
  const input = event.target as HTMLInputElement;
  if (input.id === 'pdf-file' && input.files?.[0]) selectFile(input.files[0]);
  if (input.id === 'reading-mode') state.visionMode = input.value;
  if (input.id === 'consent') {
    state.consent = input.checked;
    const button = document.querySelector<HTMLButtonElement>('#extract-button');
    if (button) button.disabled = !state.file || !state.consent || !state.health?.azure_configured;
  }
  if (input.id === 'acknowledge') {
    state.acknowledged = input.checked;
    document.querySelector<HTMLButtonElement>('#generate-button')!.disabled = !state.acknowledged;
  }
});
document.addEventListener('dragover', event => {
  event.preventDefault();
  const zone = (event.target as Element).closest('#dropzone');
  zone?.classList.add('dragging');
});
document.addEventListener('dragleave', event => (event.target as Element).closest('#dropzone')?.classList.remove('dragging'));
document.addEventListener('drop', event => {
  event.preventDefault();
  const zone = (event.target as Element).closest('#dropzone');
  zone?.classList.remove('dragging');
  if (zone && !state.busy && event.dataTransfer?.files[0]) selectFile(event.dataTransfer.files[0]);
});
sourceDialog.addEventListener('click', event => { if (event.target === sourceDialog) sourceDialog.close(); });
window.addEventListener('beforeunload', event => {
  if (state.busy) { event.preventDefault(); event.returnValue = ''; }
});

// Keep the initial loading view until configuration is known. Replacing a live
// file input while health resolves can discard its pending change event.
void api<Health>('/api/health').then(health => { state.health = health; render(); }).catch(error => { state.error = `The backend is unavailable. ${error instanceof Error ? error.message : ''}`; render(); });
