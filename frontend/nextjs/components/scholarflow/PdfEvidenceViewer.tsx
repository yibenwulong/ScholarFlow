import type { EvidenceItem } from '@/types/scholarflow';

export default function PdfEvidenceViewer({ item }: { item: EvidenceItem }) {
  const { document, passage, evidence } = item;
  const quote = evidence.locator_status === 'located' && evidence.quote_start !== null && evidence.quote_end !== null
    ? passage.text.slice(evidence.quote_start, evidence.quote_end) : evidence.quote;
  const file = `/api/scholarflow/documents/${document.id}/file`;
  const pdf = document.kind === 'pdf' && passage.page_number !== null;
  return <div className="sf-stack"><div className="sf-row"><span className="sf-badge sf-amber">{pdf ? `物理页 ${passage.page_number}` : '仅摘要依据'}</span>
    <span className={'sf-badge ' + (evidence.locator_status === 'located' ? 'sf-green' : 'sf-amber')}>{evidence.locator_status === 'located' ? '引句已定位' : '引句未定位'}</span></div>
    <div><h3>原文摘录</h3><blockquote className="sf-source-quote">{quote}</blockquote></div>
    <p className="sf-muted sf-small" style={{ overflowWrap: 'anywhere' }}>文档版本 {document.id}<br />SHA256 {document.sha256}</p>
    {pdf ? <><div className="sf-row"><a className="sf-button" href={file + '#page=' + passage.page_number} target="_blank" rel="noreferrer">打开原 PDF</a>
      <span className="sf-muted sf-small">页码从 PDF 的第一页开始计数。</span></div>
      <iframe data-testid="pdf-viewer" title="原始 PDF 文档" src={file + '#page=' + passage.page_number} className="sf-pdf" />
      <p className="sf-muted sf-small">浏览器无法预览时，打开原 PDF，按上方物理页查找摘录。</p></> : <div className="sf-notice">摘要没有真实 PDF 页码。仅依据来源明确报告的内容进行判断。</div>}
    <details><summary>查看完整原文片段</summary><pre className="sf-source-text">{passage.text}</pre></details>
  </div>;
}
