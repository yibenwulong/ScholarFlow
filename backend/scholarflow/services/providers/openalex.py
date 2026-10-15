import os

from .base import AcademicProvider, SearchResult, normalize_identifiers, source_id
from ...domain.library import PaperCandidate


def normalize_openalex(record):
    if not isinstance(record, dict) or not isinstance(record.get('title'), str) or not record['title'].strip():
        return None
    identifiers = normalize_identifiers({'doi': record.get('doi'), 'openalex': record.get('id')})
    authors = []
    for entry in record.get('authorships') or []:
        author = entry.get('author') if isinstance(entry, dict) else None
        if isinstance(author, dict) and isinstance(author.get('display_name'), str):
            authors.append(author['display_name'])
    inverted = record.get('abstract_inverted_index')
    positions = []
    if isinstance(inverted, dict):
        for word, indices in inverted.items():
            if isinstance(indices, list):
                positions.extend((i, word) for i in indices if isinstance(i, int) and not isinstance(i, bool) and i >= 0)
    abstract = ' '.join(word for _, word in sorted(positions)) or None
    location = record.get('best_oa_location')
    pdf_url = location.get('pdf_url') if isinstance(location, dict) else None
    status = 'preprint' if record.get('type') == 'preprint' else 'unknown'
    return PaperCandidate(title=record['title'].strip(), authors=authors, identifiers=identifiers,
                          year=record.get('publication_year') if isinstance(record.get('publication_year'), int) else None,
                          source='openalex', source_id=str(record.get('id') or source_id(record)),
                          abstract=abstract, pdf_url=pdf_url if isinstance(pdf_url, str) else None,
                          publication_status=status, raw_metadata=record)


class OpenAlexProvider(AcademicProvider):
    name = 'openalex'

    async def search(self, query, limit=20):
        params = {'search': query, 'per_page': min(limit, 100)}
        filters = []
        if self.year_from:
            filters.append(f'from_publication_date:{self.year_from}-01-01')
        if self.year_to:
            filters.append(f'to_publication_date:{self.year_to}-12-31')
        if filters:
            params['filter'] = ','.join(filters)
        if os.getenv('OPENALEX_API_KEY'):
            params['api_key'] = os.environ['OPENALEX_API_KEY']
        try:
            payload = (await self.request('https://api.openalex.org/works', params=params)).json()
            if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
                raise ValueError('Invalid works response')
            candidates = [candidate for item in payload['results'] if (candidate := normalize_openalex(item)) is not None]
            return SearchResult(source=self.name, query=query, candidates=candidates)
        except Exception as exc:
            return self.failure(query, exc)
