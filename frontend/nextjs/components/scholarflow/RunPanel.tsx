'use client';
import { useEffect, useState } from 'react';
import type { Run } from '@/types/scholarflow';
import { statusLabels } from '@/types/scholarflow';
import { api, messageFor } from '@/lib/scholarflow/api';
import { useRunEvents } from '@/lib/scholarflow/events';

const stages: Record<string, string> = { plan: '规划检索', approve_plan: '批准计划', search: '学术检索', screen: '筛选建议',
  confirm_selection: '确认纳入', documents: '准备原文', read: '逐篇阅读', write: '综合写作', verify: '引用核验', finish: '结束', import: '导入文献', report_check: '报告复核', export: '生成导出文件' };
export default function RunPanel({ run, onRefresh }: { run?: Run; onRefresh: () => Promise<void> }) {
  const [current, setCurrent] = useState(run), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const { events, error: connectionError } = useRunEvents(run?.id);
  const runId = run?.id;
  useEffect(() => { setCurrent(previous => previous && run && previous.id === run.id && previous.revision > run.revision ? previous : run); }, [run]);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    const poll = () => api.run(runId).then(value => { if (active) setCurrent(previous => previous?.id === value.id && previous.revision > value.revision ? previous : value); }).catch(() => {});
    const timer = setInterval(() => void poll(), 2000); return () => { active = false; clearInterval(timer); };
  }, [runId]);
  async function act(action: string) {
    if (!current) return;
    setBusy(true); setError('');
    try { setCurrent(await api.action(current, action)); await onRefresh(); } catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); }
  }
  if (!current) return null;
  return <section className="sf-card" style={{ marginTop: 24 }}><div className="sf-between"><div><h2>研究运行进度</h2>
    <div className="sf-row"><span className="sf-badge">执行：{statusLabels[current.status]}</span><span className="sf-badge">{stages[current.stage] || '处理研究任务'}</span>
      <span className="sf-badge sf-amber">{current.quality_status === 'passed' ? '质量检查通过' : current.quality_status === 'issues_present' ? '存在质量问题' : '质量尚未检查'}</span></div></div>
    <div className="sf-row">{current.status === 'failed' && <button disabled={busy} onClick={() => void act('retry')}>重试失败阶段</button>}
      {!['completed', 'cancelled'].includes(current.status) && <button disabled={busy} onClick={() => void act('cancel')}>取消运行</button>}</div></div>
    <p className="sf-muted sf-small" style={{ marginTop: 12 }}>已记录模型调用 {current.model_calls} 次。缺少实际用量或价格时，费用显示为未知。</p>
    {current.status === 'waiting_for_user' && <p className="sf-notice" style={{ marginTop: 12 }}>{current.pending_action?.type === 'approve_plan' ? '请在检索计划中批准明确的修订版本。' : '请在文献库中确认本次纳入的论文。'}</p>}
    {error && <p role="alert" className="sf-error">{error}</p>}{current.issues.map((issue, index) => <p className="sf-error" key={index}>{issue.message}</p>)}
    {connectionError && <p className="sf-muted sf-small">{connectionError}</p>}
    <details style={{ marginTop: 12 }}><summary>已保存的进度记录（{events.length}）</summary><ol className="sf-event-list">{events.map(event => <li data-testid="run-event" key={event.seq}>
      <span className="sf-muted">{event.seq}.</span> {stages[event.stage] || '研究任务'} · {event.type === 'artifact_complete' ? '阶段产物已保存' : event.type === 'finished' ? '本轮执行结束' : event.type === 'action' ? '确认操作已处理' : '进度已更新'}</li>)}</ol></details>
  </section>;
}
