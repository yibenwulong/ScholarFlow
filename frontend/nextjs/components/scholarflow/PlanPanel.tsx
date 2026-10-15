'use client';
import { useEffect, useState } from 'react';
import type { Plan, Project, Run } from '@/types/scholarflow';
import { api, messageFor, scholarRequest, write } from '@/lib/scholarflow/api';

const lines = (text: string) => text.split('\n').map(s => s.trim()).filter(Boolean);
export default function PlanPanel({ project, plans, run, onRefresh }: { project: Project; plans: Plan[]; run?: Run; onRefresh: () => Promise<void> }) {
  const latest = plans[0];
  const [base, setBase] = useState<Plan | undefined>(latest), [dirty, setDirty] = useState(false);
  const [queries, setQueries] = useState(latest?.queries.join('\n') || project.question), [inclusion, setInclusion] = useState(''), [exclusion, setExclusion] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!dirty) { setBase(latest); setQueries(latest?.queries.join('\n') || project.question);
      setInclusion((latest?.inclusion_criteria || project.inclusion_criteria).join('\n')); setExclusion((latest?.exclusion_criteria || project.exclusion_criteria).join('\n')); }
  }, [latest, dirty, project]);
  async function perform(operation: () => Promise<void>) { setBusy(true); setError(''); try { await operation(); } catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); } }
  async function save() {
    const saved = await scholarRequest<Plan>(`/projects/${project.id}/plans`, write('POST', { expected_revision: base?.revision || 0,
      queries: lines(queries), inclusion_criteria: lines(inclusion), exclusion_criteria: lines(exclusion), subquestions: base?.subquestions || [] }));
    setBase(saved); setDirty(false); setNotice('计划修订已保存'); await onRefresh();
  }
  async function approve() {
    if (!base) return;
    await scholarRequest(`/projects/${project.id}/plans/${base.id}/approve`, write('POST', { expected_revision: base.revision }));
    if (run?.status === 'waiting_for_user' && run.pending_action?.type === 'approve_plan') await api.action(run, 'continue', { plan_revision_id: base.id });
    setNotice('计划已批准'); await onRefresh();
  }
  return <div className="sf-grid"><section className="sf-card"><div className="sf-between"><h2>确定检索范围</h2><span className="sf-badge">{base ? `修订 ${base.revision}` : '尚未保存'}</span></div>
    <p className="sf-muted">审阅检索式和纳排标准，批准一个明确版本后再开始检索。</p>
    {error && <div role="alert" className="sf-error" style={{ marginTop: 16 }}>{error}</div>}{notice && <div role="status" className="sf-notice" style={{ marginTop: 16 }}>{notice}</div>}
    <label htmlFor="queries">检索式（每行一个）</label><textarea id="queries" value={queries} onChange={e => { setQueries(e.target.value); setDirty(true); }} />
    <label htmlFor="include">纳入标准</label><textarea id="include" value={inclusion} onChange={e => { setInclusion(e.target.value); setDirty(true); }} />
    <label htmlFor="exclude">排除标准</label><textarea id="exclude" value={exclusion} onChange={e => { setExclusion(e.target.value); setDirty(true); }} />
    <div className="sf-row" style={{ marginTop: 20 }}><button disabled={busy || !lines(queries).length} onClick={() => void perform(save)}>保存计划修订</button>
      <button className="sf-primary" disabled={busy || dirty || !base} onClick={() => void perform(approve)}>批准此计划</button>
      {dirty && <button onClick={() => { setDirty(false); setError(''); }}>载入服务器版本</button>}</div>
  </section><aside className="sf-stack"><div className="sf-card"><div className="sf-eyebrow">01 / PLAN</div><h2 style={{ marginTop: 10 }}>先规划，再检索</h2>
    <p className="sf-muted">你可以先让规划智能体生成检索计划，也可以直接编辑左侧内容。计划历史会保留，已开始检索的输入保持固定。</p>
    <button disabled={busy || run?.status === 'queued' || run?.status === 'running'} style={{ marginTop: 18 }} onClick={() => void perform(async () => { await api.start(project.id, { start_at: 'plan' }); setNotice('规划任务已创建'); await onRefresh(); })}>生成检索计划</button>
    {base?.approved && <button style={{ marginTop: 12 }} disabled={busy} onClick={() => void perform(async () => { await api.start(project.id, { start_at: 'search', plan_revision_id: base.id }); setNotice('检索任务已创建'); await onRefresh(); })}>按此计划检索</button>}
    </div><div className="sf-card"><h3>计划历史</h3>{plans.length ? plans.map(plan => <div key={plan.id} className="sf-between" style={{ padding: '10px 0', borderBottom: '1px solid #edf0f6' }}><span>修订 {plan.revision}</span><span className={'sf-badge ' + (plan.approved ? 'sf-green' : '')}>{plan.approved ? '已批准' : '待批准'}</span></div>) : <p className="sf-muted">还没有保存的计划。</p>}</div>
    {run?.issues.map((issue, index) => <p role="alert" className="sf-error" key={index}>{issue.message}</p>)}
  </aside></div>;
}
