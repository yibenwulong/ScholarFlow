'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import type { Plan, Project, ProjectPaper, Run, Selection } from '@/types/scholarflow';
import { statusLabels } from '@/types/scholarflow';
import { api, messageFor } from '@/lib/scholarflow/api';
import PlanPanel from './PlanPanel';
import LibraryPanel from './LibraryPanel';
import EvidencePanel from './EvidencePanel';
import ReportPanel from './ReportPanel';
import RunPanel from './RunPanel';

export default function ProjectWorkspace({ projectId }: { projectId: string }) {
  const search = useSearchParams(), tab = search.get('tab') || 'plan';
  const [project, setProject] = useState<Project>(), [plans, setPlans] = useState<Plan[]>([]);
  const [papers, setPapers] = useState<ProjectPaper[]>([]), [runs, setRuns] = useState<Run[]>([]), [selections, setSelections] = useState<Selection[]>([]);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    try {
      const [p, pl, pa, r, s] = await Promise.all([api.project(projectId), api.plans(projectId), api.papers(projectId), api.runs(projectId), api.selections(projectId)]);
      setProject(p); setPlans(pl.items); setPapers(pa.items); setRuns(r.items); setSelections(s.items); setError('');
    } catch (failure) { setError(messageFor(failure)); }
  }, [projectId]);
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 3000); return () => clearInterval(timer); }, [refresh]);
  const researchRun = runs.find(run => run.kind === 'literature_review');
  if (!project) return <div className="sf-container">{error ? <div role="alert" className="sf-error">{error}</div> : '正在载入研究项目…'}</div>;
  return <div className="sf-container"><Link href="/projects" className="sf-muted sf-small">← 全部项目</Link>
    <div className="sf-between" style={{ marginTop: 12 }}><div><div className="sf-eyebrow">LITERATURE RESEARCH</div><h1>{project.question}</h1><p className="sf-muted">{project.report_type === 'method_comparison' ? '方法比较' : '主题综述'} · {project.year_from || '不限起始'} — {project.year_to || '不限截止'}</p></div>
      {researchRun && <span className="sf-badge sf-amber">{statusLabels[researchRun.status]}</span>}</div>
    <div className="sf-stats"><div className="sf-stat"><span className="sf-muted sf-small">候选文献</span><strong>{papers.length}</strong></div>
      <div className="sf-stat"><span className="sf-muted sf-small">人工纳入</span><strong>{papers.filter(p => p.human_decision === 'included').length}</strong></div>
      <div className="sf-stat"><span className="sf-muted sf-small">检索计划</span><strong>{plans.length}</strong></div></div>
    <nav className="sf-tabs" aria-label="项目工作区">{[['plan', '检索计划'], ['papers', '文献库'], ['evidence', '证据与矩阵'], ['report', '研究报告']].map(([key, label]) =>
      <Link key={key} href={`/projects/${projectId}?tab=${key}`} aria-current={tab === key ? 'page' : undefined}>{label}</Link>)}</nav>
    {error && <p role="alert" className="sf-error">{error}</p>}
    {tab === 'plan' && <PlanPanel project={project} plans={plans} run={researchRun} onRefresh={refresh} />}
    {tab === 'papers' && <LibraryPanel projectId={projectId} papers={papers} selections={selections} plans={plans} run={researchRun} onRefresh={refresh} />}
    {tab === 'evidence' && <EvidencePanel projectId={projectId} paperId={search.get('paper') || papers[0]?.paper_id || ''} papers={papers}
      claimId={search.get('claim') || undefined} evidenceId={search.get('evidence') || undefined} />}
    {tab === 'report' && <ReportPanel projectId={projectId} runs={runs} onRefresh={refresh} />}
    <RunPanel run={runs[0]} onRefresh={refresh} />
  </div>;
}
