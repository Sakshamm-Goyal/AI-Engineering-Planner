export interface Health {
  status: string; azure_configured: boolean; deployment: string;
  max_file_mb: number; max_pages: number; max_vision_pages: number; pipeline_timeout_seconds: number;
}
export interface Usage {calls: number; input_tokens: number; output_tokens: number}
export interface SourcePage {number: number; text: string; native_text: string; method: 'native' | 'ocr' | 'vision'; warnings: string[]}
export interface Evidence {page: number; quote: string}
export interface Requirement {id: string; title: string; description: string; kind: string; acceptance_criteria: string[]; evidence: Evidence[]}
export interface Question {id: string; question: string; why_it_matters: string; requirement_ids: string[]; blocking: boolean}
export interface Extraction {
  document_id: string; filename: string; pages: SourcePage[];
  document: {title: string; summary: string; requirements: Requirement[]; questions: Question[]; out_of_scope: string[]};
  warnings: string[]; usage: Usage; mode: 'live' | 'sample';
}
export interface Review {implementation_context: string; corrections: {requirement_id: string; description: string}[]; answers: {question_id: string; answer: string}[]; acknowledge_warnings: boolean}
export interface Decision {decision: string; rationale: string; basis: string}
export interface Task {
  id: string; title: string; description: string; requirement_ids: string[]; dependencies: string[];
  complexity: 'S' | 'M' | 'L'; complexity_reason: string; release_target: string;
  priority: 'must' | 'should' | 'could' | 'unspecified'; implementation_steps: string[];
  acceptance_criteria: string[]; test_cases: string[]; out_of_scope: string[]; blocked_by: string[];
  order: number; wave: number; readiness: 'ready' | 'waiting' | 'blocked'; agent_prompt: string;
}
export interface Result {
  schema_version: string; mode: 'live' | 'sample'; generated_at: string; deployment: string;
  extraction: Extraction; review: Review; usage: Usage; warnings: string[]; markdown: string;
  plan: {project_title: string; summary: string; technical_context: {stack: string[]; approach: string; interfaces: string[]; decisions: Decision[]};
    assumptions: string[]; open_questions: Question[]; tasks: Task[];
    coverage: {requirement_id: string; task_ids: string[]; status: string}[]};
}
