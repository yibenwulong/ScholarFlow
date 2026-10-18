'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ExportJob, Page, ReportBlock, ReportDetail, ReportVersion, Run } from '@/types/scholarflow';
import { api, keyFor, messageFor, scholarRequest, write } from '@/lib/scholarflow/api';
import { latestResearchRun } from '@/lib/scholarflow/runs';
import CitationInspector from './CitationInspector';
import CitationBinder from './CitationBinder';
import ReportPreview from './ReportPreview';

export default function ReportPanel({ projectId, runs, onRefresh }: { projectId: string; runs: Run[]; onRefresh: () => Promise<void> }) {
  const [versions, setVersions] = useState<ReportVersion[]>([]), [selected, setSelected] = useState<string>();
  const [detail, setDetail] = useState<ReportDetail>(), [blocks, setBlocks] = useState<ReportBlock[]>([]), [active, setActive] = useState(0);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [listError, setListError] = useState(''), [detailError, setDetailError] = useState('');
  const [preview, setPreview] = useState(true), [mobile, setMobile] = useState<'editor' | 'citations'>('editor');
  const [format, setFormat] = useState<'markdown' | 'docx' | 'bibtex'>('markdown'), [exports, setExports] = useState<ExportJob[]>([]);
  const editor = useRef<HTMLTextAreaElement>(null);
  const [confirmedBlocks, setConfirmedBlocks] = useState<Set<string>>(new Set());
  const dirtyBlocks = useMemo(() => new Set(blocks.filter(block => { const base = detail?.version.blocks.find(b => b.id === block.id); return !base || base.text !== block.text || base.heading !== block.heading || JSON.stringify(base.claims) !== JSON.stringify(block.claims); }).map(b => b.id)), [blocks, detail]);
  const dirty = dirtyBlocks.size > 0;
  const locked = busy || selected !== detail?.version.id;
  const load = useCallback(async () => {
    try { const list = await scholarRequest<Page<ReportVersion>>(`/projects/${projectId}/reports`); setVersions(list.items); setSelected(id => id || list.items[0]?.id); setListError(''); }
    catch (failure) { setListError(messageFor(failure)); }
  }, [projectId]);
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 3000); return () => clearInterval(timer); }, [load]);
  useEffect(() => {
    if (!selected) return;
    let alive = true, polling = false, initialized = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const value = await scholarRequest<ReportDetail>(`/reports/${selected}`);
        if (!alive) return;
        if (!initialized) {
          initialized = true;
          setDetail(value); setActive(0); setConfirmedBlocks(new Set());
          let cached: ReportBlock[] | undefined;
          try { const raw = sessionStorage.getItem('sf-report:' + selected); if (raw) { const values: unknown = JSON.parse(raw); if (Array.isArray(values) && values.length === value.version.blocks.length && values.every((v, i) => v && typeof v === 'object' && 'text' in v && typeof v.text === 'string' && 'id' in v && v.id === value.version.blocks[i].id)) cached = value.version.blocks.map((block, i) => ({ ...block, text: String(values[i].text) })); } } catch {}
          setBlocks(cached || value.version.blocks);
        } else {
          setDetail(previous => previous?.version.id === value.version.id ? { ...previous, verification: value.verification, outdated: value.outdated } : previous);
        }
        setDetailError('');
      } catch (failure) { if (alive) setDetailError(messageFor(failure)); }
      finally { polling = false; }
    };
    void poll(); const timer = setInterval(() => void poll(), 2500); return () => { alive = false; clearInterval(timer); };
  }, [selected]);
  useEffect(() => { if (selected && detail?.version.id === selected) { try { if (dirty) sessionStorage.setItem('sf-report:' + selected, JSON.stringify(blocks)); else sessionStorage.removeItem('sf-report:' + selected); } catch { setNotice('浏览器缓存不可用，请及时保存本地修改'); } } }, [blocks, selected, detail, dirty]);
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    const poll = async () => {
      try {
        const files = await scholarRequest<Page<ExportJob>>(`/reports/${selected}/exports`);
        if (alive) setExports(files.items);
      } catch {}
    };
    void poll(); const timer = setInterval(() => void poll(), 2500); return () => { alive = false; clearInterval(timer); };
  }, [selected]);
  const text = blocks[active]?.text || '';
  async function perform(operation: () => Promise<void>) { setBusy(true); setError(''); try { await operation(); } catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); } }
  async function save() {
    if (!detail) return;
    const version = detail.version;
    const result = await scholarRequest<ReportVersion>(`/projects/${projectId}/reports`, write('POST', { expected_revision: version.revision,
      confirmed_binding_blocks: Array.from(confirmedBlocks),
      draft: { run_id: version.run_id, title: version.title, blocks, bindings: version.bindings, manifest: version.manifest, occurrences: [] } }));
    try { sessionStorage.removeItem('sf-report:' + version.id); } catch {} setSelected(result.id); setNotice('已保存为新报告版本，需要重新核验修改内容'); await load(); await onRefresh();
  }
  const acceptanceRun = runs.find(run => run.pending_action?.report_version_id === selected && run.pending_action?.verification_record_id === detail?.verification?.id && run.status === 'needs_review');
  const acceptedRun = runs.find(run => {
    if (run.status !== 'completed' || typeof run.manifests.acceptance_id !== 'string') return false;
    const target = run.manifests.final_report || run.manifests.result;
    return target && typeof target === 'object' && 'report_version_id' in target && target.report_version_id === selected && 'verification_record_id' in target && target.verification_record_id === detail?.verification?.id;
  });
  const canExport = !locked && !dirty && detail?.verification && (detail.verification.quality_status === 'passed' || acceptedRun);
  const research = latestResearchRun(runs);
  const emptyMessage = research?.status === 'failed' ? '本次研究未生成可显示的报告，请查看下方运行进度中的失败原因。'
    : research?.stage === 'write' && research.status === 'queued' ? '报告写作已排队，完成后会自动显示。请查看下方运行进度。'
    : research?.stage === 'write' && research.status === 'running' ? '正在撰写报告，生成后会自动显示。请查看下方运行进度。'
    : '完成纳入文献的阅读与综合后，报告将在这里生成。';
  const failure = error || detailError || listError;
  if (!detail) return <div role={failure ? 'alert' : 'status'} className="sf-card sf-empty">{failure || (versions.length ? '正在载入报告…' : emptyMessage)}</div>;
  return <div className="sf-stack"><section className="sf-card"><div className="sf-between"><div><h2>{detail.version.title}</h2><p className="sf-muted sf-small">报告版本 {detail.version.revision} · {detail.version.id}</p></div>
    <select disabled={locked} aria-label="报告版本" style={{ maxWidth: 210 }} value={selected} onChange={e => { if (dirty) setError('请先保存或放弃本地修改，再切换版本'); else setSelected(e.target.value); }}>{versions.map(v => <option value={v.id} key={v.id}>版本 {v.revision}</option>)}</select></div>
    <div className="sf-row" style={{ marginTop: 14 }}><span className="sf-badge sf-amber">{detail.verification?.quality_status === 'passed' ? '质量检查通过' : detail.verification ? '存在质量问题' : '尚未核验'}</span>
      {dirty && <><span className="sf-badge sf-amber">存在未保存修改</span><span className="sf-badge sf-amber">引用待复核</span></>}
      {detail.outdated && <span className="sf-badge sf-amber">报告已过期：文献版本或提取项有更新</span>}</div>
    {failure && <p role="alert" className="sf-error" style={{ marginTop: 14 }}>{failure}</p>}{notice && <p role="status" className="sf-notice" style={{ marginTop: 14 }}>{notice}</p>}
    <div className="sf-row" style={{ marginTop: 16 }}><button disabled={!dirty || locked} onClick={() => void perform(save)}>保存新报告版本</button>
      {dirty && <button disabled={locked} onClick={() => { setBlocks(detail.version.blocks); setConfirmedBlocks(new Set()); setError(''); }}>放弃本地修改</button>}
      <button disabled={dirty || locked} onClick={() => void perform(async () => { await scholarRequest(`/reports/${selected}/checks`, write('POST', {}, keyFor())); setNotice('独立复核任务已创建'); await onRefresh(); })}>复核此版本</button>
      {acceptanceRun && <button disabled={dirty || locked} onClick={() => void perform(async () => { await api.action(acceptanceRun, 'accept_draft', { report_version_id: detail.version.id, verification_record_id: detail.verification!.id }); setNotice('已确认此问题草稿，质量问题记录会保留'); await onRefresh(); })}>接受此版本草稿</button>}
      <select aria-label="导出格式" style={{ width: 135 }} value={format} onChange={e => setFormat(e.target.value as typeof format)}><option value="markdown">Markdown</option><option value="docx">Word / DOCX</option><option value="bibtex">BibTeX</option></select>
      <button disabled={!canExport || locked} onClick={() => void perform(async () => { await scholarRequest(`/reports/${selected}/exports`, write('POST', { format, verification_id: detail.verification!.id, draft_acceptance_id: acceptedRun?.manifests.acceptance_id || null }, keyFor())); setNotice('导出任务已创建，完成后可下载'); await onRefresh(); })}>导出已保存版本</button>
      {exports.filter(job => job.status === 'ready').map(job => <a className="sf-button" href={`/api/scholarflow/exports/${job.id}/file`} key={job.id}>下载 {job.format}{job.draft ? ' 草稿' : ''}</a>)}</div>
  </section><div className="sf-row"><button onClick={() => setMobile('editor')}>报告正文</button><button onClick={() => setMobile('citations')}>引用核验</button></div>
    <div className="sf-report-layout"><nav className={'sf-card sf-report-outline ' + (mobile !== 'editor' ? 'sf-mobile-hidden' : '')} aria-label="报告大纲"><h3>报告大纲</h3>{blocks.map((block, index) => <button className={active === index ? 'sf-outline-active' : ''} key={block.id} onClick={() => setActive(index)}>{index + 1}. {block.heading || '正文'}</button>)}</nav>
      <section className={'sf-card sf-report-editor ' + (mobile !== 'editor' ? 'sf-mobile-hidden' : '')}><div className="sf-between"><h3>{blocks[active]?.heading || '正文'}</h3><button onClick={() => setPreview(value => !value)}>{preview ? '编辑正文' : '预览正文'}</button></div>
        {preview ? <ReportPreview blocks={blocks} bindings={detail.version.bindings} active={active} /> : <><label htmlFor="report-body">报告正文</label><textarea id="report-body" ref={editor} disabled={locked} className="sf-report-textarea" value={text} onChange={e => { const id = blocks[active].id; setConfirmedBlocks(previous => new Set(Array.from(previous).filter(item => item !== id))); setBlocks(previous => previous.map((block, index) => index === active ? { ...block, text: e.target.value, claims: [] } : block)); }} />
<CitationBinder key={selected + ":" + blocks[active]?.id} versionId={detail.version.id} block={blocks[active]} bindings={detail.version.bindings} editor={editor} disabled={locked} onChange={value => setBlocks(previous => previous.map(block => block.id === value.id ? value : block))} onConfirm={() => setConfirmedBlocks(previous => new Set([...Array.from(previous), blocks[active].id]))} />
        <p className="sf-muted sf-small" style={{ marginTop: 14 }}>编辑时保留原始引用标记，预览会显示文献编号。正文修改后，需要重新绑定或复核对应的引用。</p></>}
      </section><div className={'sf-report-citations ' + (mobile !== 'citations' ? 'sf-mobile-hidden' : '')}><CitationInspector detail={detail} dirtyBlocks={dirtyBlocks} /></div>
    </div></div>;
}
