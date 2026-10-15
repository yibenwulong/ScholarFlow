import os

from .base import AcademicProvider, SearchResult, normalize_identifiers, source_id
from ...domain.library import PaperCandidate


def normalize_semantic(record):
    if not isinstance(record, dict) or not isinstance(record.get('title'), str) or not record['title'].strip():
        return None
    identifiers = normalize_identifiers(record.get('externalIds') if isinstance(record.get('externalIds'), dict) else {})
    if record.get('paperId'):
        identifiers['semantic_scholar'] = str(record['paperId'])
    pdf = record.get('openAccessPdf')
    authors = [a['name'] for a in (record.get('authors') or [])
               if isinstance(a, dict) and isinstance(a.get('name'), str)]
    types = record.get('publicationTypes') or []
    status = 'preprint' if 'Preprint' in types else 'published' if 'JournalArticle' in types else 'unknown'
    return PaperCandidate(title=record['title'].strip(), authors=authors,
                          year=record.get('year') if isinstance(record.get('year'), int) else None,
                          identifiers=identifiers, source='semantic_scholar',
                          source_id=str(record.get('paperId') or source_id(record)),
                          abstract=record.get('abstract') if isinstance(record.get('abstract'), str) else None,
                          pdf_url=pdf.get('url') if isinstance(pdf, dict) and isinstance(pdf.get('url'), str) else None,
                          publication_status=status, raw_metadata=record)


class SemanticScholarProvider(AcademicProvider):
    name = 'semantic_scholar'

    async def search(self, query, limit=20):
        params = {'query': query, 'limit': min(limit, 100),
                  'fields': 'title,abstract,year,authors,externalIds,isOpenAccess,openAccessPdf,publicationTypes,venue'}
        if self.year_from or self.year_to:
            params['year'] = f'{self.year_from or ""}-{self.year_to or ""}'
        headers = {'x-api-key': os.environ['SEMANTIC_SCHOLAR_API_KEY']} if os.getenv('SEMANTIC_SCHOLAR_API_KEY') else {}
        try:
            payload = (await self.request('https://api.semanticscholar.org/graph/v1/paper/search', params=params, headers=headers)).json()
            if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                raise ValueError('Invalid paper search response')
            candidates = [candidate for item in payload['data'] if (candidate := normalize_semantic(item)) is not None]
            return SearchResult(source=self.name, query=query, candidates=candidates)
        except Exception as exc:
            return self.failure(query, exc)
