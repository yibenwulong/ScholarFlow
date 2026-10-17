'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { PaperReadingStatus, ProjectPaper, Run } from '@/types/scholarflow';
import { api, messageFor, ScholarApiError } from '@/lib/scholarflow/api';

const labels = { queued: '排队中', running: '正在阅读', succeeded: '阅读成功', failed: '阅读失败', cancelled: '阅读已取消' };
const blockReasons: Record<string, string> = {
  'Only a failed Reader task can be retried': '此篇当前不是阅读失败状态，无需重试。',
  'The selected paper version has changed': '文献版本已变更，请重新确认要分析的版本。',
  'An original document is no longer available for reading': '原文已不可用，请先到文献库补充可解析的 PDF。',
  'A human extraction revision must be preserved': '此篇已有人工修订，为保留你的修改，暂不允许重新阅读。',
  'The original Reader inputs are no longer available': '原阅读输入已不可用，请重新选择文献后分析。',
  'A retry is already queued or running': '该论文已有单篇重试任务正在运行',
  'This paper has already been read successfully': '此篇已有成功阅读结果，无需重试。',
  'The latest retry has not failed': '当前重试尚未失败，无需再次提交。',
  'The latest paper retry has not failed': '当前重试尚未失败，无需再次提交。',
};
const blockReason = (reason: string) => blockReasons[reason] || (/[\u3400-\u9fff]/.test(reason) ? reason : '暂不能重试此篇，请检查原文和运行状态。');
function readingError(error: PaperReadingStatus['error']) {
  if (error?.code === 'invalid_model_output') return '阅读结果格式不完整，可仅重试此篇。';
  if (['no_usable_text', 'missing_document', 'document_not_ready'].includes(error?.code || '')) return '原文暂不可读，请先到文献库补充可解析的 PDF。';
  return error?.message && /[\u3400-\u9fff]/.test(error.message) ? error.message : '此篇阅读失败，已成功的其他论文仍可查看。';
}

export default function ReadingStatusPanel({ projectId, papers, onRefresh }: {
  projectId: string; papers: ProjectPaper[]; onRefresh?: () => Promise<void>;
}) {
  const [items, setItems] = useState<PaperReadingStatus[]>([]), [loadError, setLoadError] = useState(''), [retryError, setRetryError] = useState('');
  const [localRuns, setLocalRuns] = useState<Record<string, Run>>({}), [submitting, setSubmitting] = useState<string[]>([]);
  const localRef = useRef<Record<string, Run>>({}), submittingRef = useRef(new Set<string>());
  const remember = useCallback((jobId: string, run?: Run) => {
    const next = { ...localRef.current };
    if (run) next[jobId] = run; else delete next[jobId];
    localRef.current = next; setLocalRuns(next);
  }, []);
  useEffect(() => {
    let alive = true, polling = false;
    async function poll() {
      if (polling) return;
      polling = true;
      try {
        const result = await api.readingStatus(projectId);
        if (!alive) return;
        setItems(result.items); setLoadError('');
        await Promise.all(Object.entries(localRef.current).map(async ([jobId, local]) => {
          const item = result.items.find(row => row.source_job_id === jobId);
          if (item?.status === 'succeeded' || item?.active_retry_run_id === local.id) {
            if (localRef.current[jobId]?.id === local.id) remember(jobId);
            return;
          }
          // The source job stays fixed across retries. Consult the new run to distinguish
          // an old failed status response from a retry that has itself already failed.
          try {
            const current = await api.run(local.id);
            if (!alive || localRef.current[jobId]?.id !== local.id) return;
            if (['failed', 'cancelled'].includes(current.status)) remember(jobId);
            else remember(jobId, current);
          } catch { /* Keep the local lock until the server confirms this retry's state. */ }
        }));
      } catch (failure) { if (alive) setLoadError(messageFor(failure)); }
      finally { polling = false; }
    }
    void poll(); const timer = setInterval(() => void poll(), 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [projectId, remember]);
  async function retry(item: PaperReadingStatus) {
    const jobId = item.source_job_id;
    if (!item.can_retry || submittingRef.current.has(jobId) || localRef.current[jobId]) return;
    submittingRef.current.add(jobId); setSubmitting(Array.from(submittingRef.current)); setRetryError('');
    try {
      const { run } = await api.retryReading(projectId, jobId);
      remember(jobId, run);
      void onRefresh?.().catch(() => {});
    } catch (failure) { setRetryError(failure instanceof ScholarApiError && failure.code === 'reading_retry_blocked'
      ? blockReason(failure.message) : messageFor(failure)); }
    finally { submittingRef.current.delete(jobId); setSubmitting(Array.from(submittingRef.current)); }
  }
  const displayed = items.map(item => ({ ...item, status: localRuns[item.source_job_id]
    ? localRuns[item.source_job_id].status === 'running' ? 'running' as const : 'queued' as const : item.status }));
  const successful = displayed.filter(item => item.status === 'succeeded'), remaining = displayed.filter(item => item.status !== 'succeeded');
  const localStates = Object.values(localRuns);
  const localNotice = localStates.some(run => run.status === 'queued') ? '单篇重试已排队，只补充阅读结果，不会自动生成报告。'
    : localStates.some(run => run.status === 'running') ? '正在重试单篇阅读，只补充阅读结果，不会自动生成报告。'
    : '单篇阅读已完成，正在同步结果。';
  const titleFor = (item: PaperReadingStatus) => papers.find(paper => paper.paper_version_id === item.paper_version_id)?.version.title || item.paper_version_id;
  if (!items.length && !loadError && !retryError) return null;
  return <section className="sf-card sf-stack" aria-label="逐篇阅读状态"><div><h2>逐篇阅读状态</h2>
    <p className="sf-muted" style={{ marginTop: 8 }}>成功的阅读结果会先显示在下方矩阵。单篇重试只补充阅读结果，不会自动生成报告。</p></div>
    <div className="sf-row"><span className="sf-badge sf-green">已成功 {successful.length} 篇</span><span className="sf-badge">失败 {displayed.filter(item => item.status === 'failed').length} 篇</span>
      {displayed.some(item => ['queued', 'running'].includes(item.status)) && <span className="sf-badge">处理中 {displayed.filter(item => ['queued', 'running'].includes(item.status)).length} 篇</span>}</div>
    {loadError && <p role="alert" className="sf-error">{loadError}</p>}{retryError && <p role="alert" className="sf-error">{retryError}</p>}
    {localStates.length > 0 && <p role="status" className="sf-notice">{localNotice}</p>}
    {remaining.map(item => { const title = titleFor(item), local = localRuns[item.source_job_id], busy = submitting.includes(item.source_job_id);
      return <article key={item.source_job_id} className={item.status === 'failed' ? 'sf-error' : 'sf-notice'}><div className="sf-row"><strong style={{ overflowWrap: 'anywhere' }}>{title}</strong>
        <span className="sf-badge">{local?.status === 'completed' ? '结果同步中' : labels[item.status]}</span></div>
        {item.status === 'failed' && <><p style={{ marginTop: 8 }}>{readingError(item.error)}</p>{item.error && <details style={{ marginTop: 8 }}><summary>错误详情</summary><p className="sf-small" style={{ overflowWrap: 'anywhere' }}>{item.error.message}</p></details>}</>}
        {!local && item.status === 'failed' && item.retry_block_reason && <p className="sf-small" style={{ marginTop: 8 }}>{blockReason(item.retry_block_reason)}</p>}
        {(item.can_retry || local || busy) && <button style={{ marginTop: 12 }} aria-label={'仅重试此篇 ' + title} disabled={!item.can_retry || !!local || busy}
          onClick={() => void retry(item)}>{busy ? '正在提交…' : local ? local.status === 'running' ? '正在重试此篇' : local.status === 'completed' ? '正在同步结果' : '重试已排队' : '仅重试此篇'}</button>}
      </article>;
    })}
    {successful.length > 0 && <details><summary>已完成论文（{successful.length}）</summary><ul style={{ marginTop: 10 }}>{successful.map(item => <li key={item.source_job_id} style={{ overflowWrap: 'anywhere' }}>{titleFor(item)}</li>)}</ul></details>}
  </section>;
}
