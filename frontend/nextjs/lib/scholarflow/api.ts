import type { EvidenceBundle, Extraction, Page, PaperReadingStatus, Plan, Project, ProjectPaper, Run, Selection } from '@/types/scholarflow';

export class ScholarApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: unknown) { super(message); }
}
export function messageFor(error: unknown): string {
  if (error instanceof ScholarApiError && error.status === 409) return `${error.message}。你的输入已保留，请载入最新版本后重新保存。`;
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}
export async function scholarRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch('/api/scholarflow' + path, { ...options, headers, cache: 'no-store' });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = body && typeof body === 'object' && 'error' in body ? body.error : null;
    const error = envelope && typeof envelope === 'object' ? envelope as Record<string, unknown> : {};
    throw new ScholarApiError(response.status, String(error.code || 'http_error'), String(error.message || '服务器未能完成请求'), error.details);
  }
  return body as T;
}
export const write = (method: string, body: unknown, key?: string): RequestInit => ({ method, body: JSON.stringify(body),
  headers: key ? { 'Idempotency-Key': key } : undefined });
export const keyFor = () => crypto.randomUUID();
export const api = {
  projects: () => scholarRequest<Page<Project>>('/projects'),
  project: (id: string) => scholarRequest<Project>(`/projects/${id}`),
  plans: (id: string) => scholarRequest<Page<Plan>>(`/projects/${id}/plans`),
  papers: (id: string) => scholarRequest<Page<ProjectPaper>>(`/projects/${id}/papers`),
  selections: (id: string) => scholarRequest<Page<Selection>>(`/projects/${id}/selections`),
  runs: (id: string) => scholarRequest<Page<Run>>(`/projects/${id}/runs`),
  extractions: (id: string) => scholarRequest<Page<Extraction>>(`/projects/${id}/extractions`),
  readingStatus: (id: string) => scholarRequest<Page<PaperReadingStatus>>(`/projects/${id}/reading-status`),
  retryReading: (id: string, sourceJobId: string) => scholarRequest<{ run: Run }>(`/projects/${id}/reading-retries`,
    write('POST', { source_job_id: sourceJobId }, keyFor())),
  evidence: (id: string, signal?: AbortSignal) => scholarRequest<EvidenceBundle>(`/claims/${id}/evidence`, { signal }),
  run: (id: string) => scholarRequest<Run>(`/runs/${id}`),
  start: (id: string, input: { start_at: string; plan_revision_id?: string; selection_id?: string }) =>
    scholarRequest<Run>(`/projects/${id}/runs`, write('POST', input, keyFor())),
  action: async (run: Run, action: string, inputs: Record<string, string> = {}) => {
    const current = await api.run(run.id);
    return scholarRequest<Run>(`/runs/${run.id}/actions`, write('POST', { action, expected_revision: current.revision, ...inputs }, keyFor()));
  },
};
