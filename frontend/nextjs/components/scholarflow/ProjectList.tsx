'use client';
import { FormEvent, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import type { Project, Source } from '@/types/scholarflow';
import { api, messageFor, scholarRequest, write } from '@/lib/scholarflow/api';

const sourceNames: Record<Source, string> = { arxiv: 'arXiv', semantic_scholar: 'Semantic Scholar', openalex: 'OpenAlex' };
const lines = (value: string) => value.split('\n').map(s => s.trim()).filter(Boolean);
export default function ProjectList() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]), [loading, setLoading] = useState(true);
  const [question, setQuestion] = useState(''), [reportType, setReportType] = useState('topic_review');
  const [from, setFrom] = useState(''), [to, setTo] = useState(''), [criteria, setCriteria] = useState('');
  const [sources, setSources] = useState<Source[]>(['arxiv', 'semantic_scholar', 'openalex']);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => { api.projects().then(data => setProjects(data.items)).catch(e => setError(messageFor(e))).finally(() => setLoading(false)); }, []);
  async function create(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const result = await scholarRequest<Project>('/projects', write('POST', { question, report_type: reportType,
        sources, language: 'zh-CN', year_from: from ? Number(from) : null, year_to: to ? Number(to) : null, inclusion_criteria: lines(criteria) }));
      router.push('/projects/' + result.id);
    } catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); }
  }
  return <div className="sf-container"><div className="sf-eyebrow">RESEARCH WORKSPACE</div><h1>把文献调研，变成可复核的研究。</h1>
    <p className="sf-muted" style={{ marginBottom: 30 }}>规划检索范围、比较研究方法，让报告中的每个判断都有原文可查。</p>
    {error && <p role="alert" className="sf-error" style={{ marginBottom: 18 }}>{error}</p>}
    <div className="sf-grid"><form onSubmit={create} className="sf-card"><fieldset disabled={loading} style={{ border: 0, padding: 0, minWidth: 0 }}><h2>开始一个研究项目</h2><p className="sf-muted">一个清晰的问题，是文献调研的起点。</p>
      <label htmlFor="question">研究问题</label><textarea id="question" required maxLength={10000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：多智能体系统在科研文献综述中有哪些方法，如何评估它们的可靠性？" />
      <label htmlFor="reportType">报告类型</label><select id="reportType" value={reportType} onChange={e => setReportType(e.target.value)}><option value="topic_review">主题综述</option><option value="method_comparison">方法比较</option></select>
      <div className="sf-grid" style={{ gap: 14 }}><div><label htmlFor="yearFrom">起始年份</label><input id="yearFrom" type="number" min="1000" max="9999" value={from} onChange={e => setFrom(e.target.value)} placeholder="不限" /></div>
        <div><label htmlFor="yearTo">截止年份</label><input id="yearTo" type="number" min="1000" max="9999" value={to} onChange={e => setTo(e.target.value)} placeholder="不限" /></div></div>
      <label>学术来源</label><div className="sf-row">{(Object.keys(sourceNames) as Source[]).map(source => <label key={source} className="sf-check-label sf-small"><input type="checkbox" checked={sources.includes(source)} onChange={e => setSources(current => e.target.checked ? [...current, source] : current.filter(s => s !== source))} />{sourceNames[source]}</label>)}</div>
      <label htmlFor="criteria">初始纳入标准（每行一条）</label><textarea id="criteria" value={criteria} onChange={e => setCriteria(e.target.value)} placeholder="研究问题相关；提供明确的方法或评估" />
      <button className="sf-primary" disabled={busy || !sources.length} style={{ marginTop: 20, width: '100%' }}>{busy ? '正在创建…' : '创建项目'}</button>
    </fieldset></form><section className="sf-stack"><div className="sf-between"><h2 style={{ margin: 0 }}>我的研究项目</h2><span className="sf-badge">{projects.length} 个项目</span></div>
      {loading ? <div className="sf-empty">正在载入项目…</div> : projects.length ? projects.map(project => <Link href={'/projects/' + project.id} className="sf-card sf-project-link" key={project.id}>
        <span className="sf-badge">{project.report_type === 'method_comparison' ? '方法比较' : '主题综述'}</span><h3 style={{ marginTop: 12 }}>{project.question}</h3>
        <p className="sf-muted sf-small">{project.sources.map(source => sourceNames[source]).join(' · ')}</p><p className="sf-muted sf-small" style={{ marginTop: 12 }}>{new Date(project.created_at).toLocaleDateString()} 创建　进入项目 →</p></Link>) :
        <div className="sf-card sf-empty"><h3>这里将保存你的研究过程</h3><p>计划、纳入的文献、比较矩阵和报告版本都归属于项目。</p></div>}
    </section></div></div>;
}
