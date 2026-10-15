import asyncio
import re
import time
from xml.etree import ElementTree

from .base import AcademicProvider, SearchResult, normalize_identifiers
from ...domain.library import PaperCandidate

ATOM = '{http://www.w3.org/2005/Atom}'
ARXIV = '{http://arxiv.org/schemas/atom}'


def normalize_arxiv(xml):
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('XML document declarations are not accepted')
    root = ElementTree.fromstring(xml)
    results = []
    for entry in root.findall(ATOM + 'entry'):
        url = entry.findtext(ATOM + 'id') or ''
        title = (entry.findtext(ATOM + 'title') or '').strip()
        if not title or 'arxiv.org/abs/' not in url:
            continue
        ids = normalize_identifiers({'arxiv': url, 'doi': entry.findtext(ARXIV + 'doi')})
        pdf_url = next((link.get('href') for link in entry.findall(ATOM + 'link')
                        if link.get('title') == 'pdf' or link.get('type') == 'application/pdf'), None)
        published = entry.findtext(ATOM + 'published') or ''
        results.append(PaperCandidate(title=' '.join(title.split()),
            authors=[a.findtext(ATOM + 'name') for a in entry.findall(ATOM + 'author') if a.findtext(ATOM + 'name')],
            year=int(published[:4]) if re.match(r'^\d{4}', published) else None,
            identifiers=ids, source='arxiv', source_id=ids['arxiv'],
            abstract=entry.findtext(ATOM + 'summary'), pdf_url=pdf_url,
            publication_status='preprint', raw_metadata={'entry_url': url, 'published': published}))
    return results


class ArxivProvider(AcademicProvider):
    name = 'arxiv'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def search(self, query, limit=20):
        actual_query = query
        if self.year_from or self.year_to:
            actual_query = f'({query}) AND submittedDate:[{self.year_from or 1900}01010000 TO {self.year_to or 9999}12312359]'
        try:
            async with self._lock:
                await asyncio.sleep(max(0.0, 3 - (time.monotonic() - self._last_request)))
                self._last_request = time.monotonic()
                response = await self.request('https://export.arxiv.org/api/query', params={
                    'search_query': actual_query, 'start': 0, 'max_results': min(limit, 100), 'sortBy': 'relevance'})
            return SearchResult(source=self.name, query=actual_query, candidates=normalize_arxiv(response.text))
        except Exception as exc:
            return self.failure(actual_query, exc)
