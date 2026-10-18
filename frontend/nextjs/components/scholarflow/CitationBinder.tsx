'use client';
import { useEffect, useState, type RefObject } from 'react';
import type { CitationBinding, Page, ReportBlock } from '@/types/scholarflow';
import { messageFor, scholarRequest } from '@/lib/scholarflow/api';

interface Option { key: string; title: string; claim_revision_id: string; evidence_id: string;
  quote: string; source_scope: string; locator_status: string }

export default function CitationBinder({ versionId, block, bindings, editor, disabled, onChange, onConfirm, initialTokenIndex = 0 }: {
  versionId: string; block: ReportBlock; bindings: CitationBinding[]; editor: RefObject<HTMLTextAreaElement>;
  disabled: boolean; onChange: (block: ReportBlock) => void; onConfirm: () => void; initialTokenIndex?: number;
}) {
  const [options, setOptions] = useState<Option[]>([]), [error, setError] = useState('');
  const [token, setToken] = useState(String(initialTokenIndex)), [evidence, setEvidence] = useState(''), [insertKey, setInsertKey] = useState(bindings[0]?.key || '');
  const [span, setSpan] = useState<{ start: number; end: number }>();
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let alive = true;
    scholarRequest<Page<Option>>(`/reports/${versionId}/citation-options`).then(value => { if (alive) setOptions(value.items); })
      .catch(failure => { if (alive) setError(messageFor(failure)); });
    return () => { alive = false; };
  }, [versionId]);
  useEffect(() => { setSpan(undefined); setNotice(''); }, [block.text]);
  useEffect(() => { setToken(String(initialTokenIndex)); setEvidence(''); setSpan(undefined); setNotice(''); }, [initialTokenIndex]);
  const tokens = Array.from(block.text.matchAll(/\[@(sf_[A-Za-z0-9_]+)\]/g));
  const current = tokens[Number(token)];
  const candidates = options.filter(item => item.key === current?.[1]);
  function capture() {
    const input = editor.current;
    if (!input || input.selectionEnd <= input.selectionStart) { setError('请先在正文中选中需要核验的论断'); return; }
    setSpan({ start: Array.from(block.text.slice(0, input.selectionStart)).length,
      end: Array.from(block.text.slice(0, input.selectionEnd)).length }); setError('');
  }
  function confirm() {
    const source = candidates.find(item => item.evidence_id === evidence);
    if (!span || !current || !source || span.end > Array.from(block.text.slice(0, current.index)).length) {
      setError('请选择此引用前的论断文字及对应原文依据'); return;
    }
    const index = Number(token);
    onChange({ ...block, claims: [...block.claims.filter(item => item.token_index !== index), {
      ...span, token_index: index, claim_revision_ids: [source.claim_revision_id], evidence_ids: [source.evidence_id],
    }].sort((a, b) => a.token_index - b.token_index) });
    onConfirm(); setError(''); setNotice('此处引用已绑定，保存后仍需复核支持性');
  }
  return <fieldset disabled={disabled} style={{ marginTop: 20 }}><legend>引用与原文依据</legend>
    <p className="sf-muted sf-small">选中正文中的论断，选择引用位置和原文，再确认绑定。正文再次修改后需要重新绑定。</p>
    <div className="sf-stack" style={{ marginTop: 12 }}>
      <label>插入已有文献引用<select aria-label="插入已有文献引用" value={insertKey} onChange={e => setInsertKey(e.target.value)}>
        {bindings.map(item => <option key={item.key} value={item.key}>{String(item.bibliography_snapshot.title || '文献')}</option>)}</select></label>
      <button type="button" disabled={!insertKey || disabled} onClick={() => {
        const offset = editor.current?.selectionEnd ?? block.text.length;
        onChange({ ...block, text: block.text.slice(0, offset) + ` [@${insertKey}]` + block.text.slice(offset), claims: [] });
      }}>插入引用</button>
      <label>引用位置<select aria-label="引用位置" value={token} onChange={e => { setToken(e.target.value); setEvidence(''); }}>
        {tokens.map((item, index) => <option key={index} value={index}>引用 {index + 1} · {String(bindings.find(b => b.key === item[1])?.bibliography_snapshot.title || '文献')}</option>)}</select></label>
      <button type="button" onClick={capture}>使用所选文字作为论断</button>
      {span && <blockquote className="sf-source-quote">{Array.from(block.text).slice(span.start, span.end).join('')}</blockquote>}
      <label>原文依据<select aria-label="原文依据" value={evidence} onChange={e => setEvidence(e.target.value)}>
        <option value="">请选择原文</option>{candidates.map(item => <option key={item.evidence_id} value={item.evidence_id}>
          {item.source_scope === 'abstract' ? '仅摘要' : '全文'} · {item.locator_status === 'located' ? '已定位' : '未定位'} · {item.quote}</option>)}</select></label>
      <button type="button" onClick={confirm}>确认此处引用绑定</button>
      {error && <p role="alert" className="sf-error">{error}</p>}{notice && <p role="status" className="sf-notice">{notice}</p>}
    </div>
  </fieldset>;
}
