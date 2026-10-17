export interface Entity { id: string; revision: number; created_at: string }
export type Source = 'arxiv' | 'semantic_scholar' | 'openalex';
export type Decision = 'pending' | 'included' | 'excluded';
export interface Project extends Entity {
  question: string; language: string; year_from: number | null; year_to: number | null; sources: Source[];
  inclusion_criteria: string[]; exclusion_criteria: string[]; report_type: 'topic_review' | 'method_comparison';
}
export interface Plan extends Entity { project_id: string; project_revision: number; queries: string[];
  inclusion_criteria: string[]; exclusion_criteria: string[]; subquestions: string[]; approved?: boolean }
export interface Page<T> { items: T[]; next_cursor?: string | null }
export interface Selection extends Entity { project_id: string; paper_version_ids: string[] }
export type RunStatus = 'queued' | 'running' | 'waiting_for_user' | 'needs_review' | 'completed' | 'failed' | 'cancelled';
export interface Run extends Entity {
  target_id: string | null;
  project_id: string; kind: 'literature_review' | 'paper_read' | 'import' | 'report_check' | 'export'; start_at: string;
  status: RunStatus; stage: string; quality_status: string; model_calls: number;
  pending_action: { type: string; plan_revision_id?: string; report_version_id?: string; verification_record_id?: string } | null;
  manifests: Record<string, unknown>; issues: Array<{ code: string; message: string }>;
}
export interface RunEvent extends Entity { run_id: string; seq: number; type: string; stage: string; data: Record<string, unknown> }
export interface PaperVersion extends Entity { paper_id: string; title: string; authors: string[]; year: number | null;
  source: string; source_id: string; identifiers: Record<string, string>; abstract: string | null; pdf_url: string | null; publication_status: string }
export interface DocumentVersion extends Entity { paper_version_id: string; kind: 'pdf' | 'abstract'; sha256: string;
  storage_key: string | null; abstract?: string | null; original_name: string; parse_status: string; parse_error: string | null }
export interface ProjectPaper extends Entity { project_id: string; paper_id: string; paper_version_id: string;
  human_decision: Decision; reason: string; notes: string; ai_recommendation: 'included' | 'excluded' | 'uncertain';
  ai_reason: string; version: PaperVersion; documents: DocumentVersion[] }
export const decisionLabels = { pending: '待决定', included: '纳入', excluded: '排除', uncertain: '不确定' };
export const statusLabels: Record<RunStatus, string> = { queued: '排队中', running: '研究中', waiting_for_user: '等待确认',
  needs_review: '待复核', completed: '已完成', failed: '执行失败', cancelled: '已取消' };
export type ClaimStatus = 'supported' | 'inferred' | 'conflicting' | 'insufficient_evidence' | 'needs_recheck';
export const claimLabels: Record<ClaimStatus, string> = { supported: '支持', inferred: '推断', conflicting: '冲突', insufficient_evidence: '证据不足', needs_recheck: '待复核' };
export const fieldLabels = { research_question: '研究问题', method: '方法', dataset: '研究对象或数据集', findings: '主要发现', limitations: '局限' };
export type FieldName = keyof typeof fieldLabels;
export interface ExtractedField { value: string | null; claim_ids: string[]; evidence_ids: string[]; review_status: ClaimStatus }
export interface Extraction extends Entity { project_id: string; paper_version_id: string; document_version_ids: string[];
  fields: Record<FieldName, ExtractedField>; origin: 'ai' | 'human'; previous_id: string | null }
export interface PaperReadingStatus {
  paper_version_id: string; source_run_id: string; source_job_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'; extraction_id: string | null;
  error: { code: string; message: string } | null; can_retry: boolean; active_retry_run_id: string | null;
  retry_block_reason?: string | null;
}
export interface Claim extends Entity { run_id: string; text: string; source_scope: 'fulltext' | 'abstract'; status: ClaimStatus }
export interface Passage extends Entity { document_version_id: string; text: string; page_number: number | null; start: number; end: number }
export interface Evidence extends Entity { claim_revision_id: string; passage_id: string; quote: string; relation: string;
  locator_status: 'located' | 'unlocated'; quote_start: number | null; quote_end: number | null; explanation: string }
export interface EvidenceReview extends Entity { evidence_id: string; verdict: ClaimStatus; reason: string; reviewer: string }
export interface EvidenceItem { evidence: Evidence; passage: Passage; document: DocumentVersion; reviews: EvidenceReview[] }
export interface EvidenceBundle { claim: Claim; items: EvidenceItem[] }
export interface ReportClaimSpan { start: number; end: number; token_index: number; claim_revision_ids: string[]; evidence_ids: string[] }
export interface ReportBlock { id: string; heading: string; text: string; claims: ReportClaimSpan[] }
export interface CitationBinding { key: string; paper_version_id: string; bibliography_snapshot: Record<string, unknown> }
export interface CitationOccurrence extends Entity { block_id: string; token_index: number; claim_revision_ids: string[]; evidence_ids: string[];
  claim_start: number | null; claim_end: number | null }
export interface ReportVersion extends Entity { project_id: string; run_id: string; title: string; blocks: ReportBlock[];
  bindings: CitationBinding[]; occurrences: CitationOccurrence[]; manifest: Record<string, unknown>; previous_id: string | null }
export interface Check { occurrence_id?: string; block_id?: string; status: ClaimStatus; reason: string; checker: string }
export interface Verification extends Entity { report_version_id: string; quality_status: string; checker: string; checks: Check[] }
export interface ReportDetail { version: ReportVersion; verification: Verification | null; outdated: boolean;
  citations: CitationOccurrence[]; references: Array<{ number: number; key: string; title: string }>; rendered_markdown: string }
export interface ExportJob extends Entity { report_version_id: string; format: 'markdown' | 'docx' | 'bibtex'; status: string; draft: boolean }
