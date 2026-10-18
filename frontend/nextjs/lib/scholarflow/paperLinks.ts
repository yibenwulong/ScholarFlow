import type { ProjectPaper } from '@/types/scholarflow';

function webUrl(value: string | null | undefined) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}

export function paperLinks(paper: ProjectPaper): {
  pdf?: { href: string; filename?: string }; details?: string;
} {
  const version = paper.version, ids = version.identifiers;
  const arxivId = ids.arxiv || (version.source === 'arxiv' ? version.source_id : '');
  const arxiv = /^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*\/\d{7})(?:v[1-9]\d*)?$/i.test(arxivId) ? arxivId : undefined;
  const doi = /^10\.\d{4,9}\/\S+$/.test(ids.doi || '') ? ids.doi : undefined;
  const semanticId = ids.semantic_scholar || (version.source === 'semantic_scholar' ? version.source_id : '');
  const openalexId = ids.openalex || (version.source === 'openalex' ? version.source_id.replace(/^https?:\/\/openalex\.org\//i, '') : '');
  const details = arxiv ? `https://arxiv.org/abs/${arxiv}`
    : doi ? `https://doi.org/${encodeURIComponent(doi).replace(/%2F/gi, '/')}`
    : /^[a-f0-9]{40}$/i.test(semanticId) ? `https://www.semanticscholar.org/paper/${semanticId}`
    : /^W\d+$/i.test(openalexId) ? `https://openalex.org/${openalexId.toUpperCase()}` : undefined;
  const saved = paper.documents.filter(document => document.kind === 'pdf' && document.storage_key &&
    document.paper_version_id === paper.paper_version_id)
    .sort((left, right) => right.created_at.localeCompare(left.created_at) || right.revision - left.revision)[0];
  const remote = webUrl(version.pdf_url) || (arxiv ? `https://arxiv.org/pdf/${arxiv}` : undefined);
  return { details, pdf: saved ? { href: `/api/scholarflow/documents/${encodeURIComponent(saved.id)}/file`,
    filename: saved.original_name || 'paper.pdf' } : remote ? { href: remote } : undefined };
}
