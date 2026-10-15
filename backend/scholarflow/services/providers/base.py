from datetime import datetime
import hashlib
import json
import re
from urllib.parse import unquote

import httpx
from pydantic import Field

from ...domain.common import Model, utcnow
from ...domain.library import PaperCandidate


def normalize_identifiers(values):
    result = {}
    for key, value in (values or {}).items():
        if not isinstance(value, str) or not value.strip():
            continue
        key = key.lower()
        value = value.strip()
        if key == 'doi':
            value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value, flags=re.I)
            value = unquote(value).lower()
            if not re.match(r'^10\.\d{4,9}/\S+$', value):
                continue
        elif key == 'arxiv':
            value = re.sub(r'^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)', '', value, flags=re.I)
            value = value.removesuffix('.pdf').lower()
        elif key == 'openalex':
            value = value.rsplit('/', 1)[-1]
        result[key] = value
    return result


def source_id(record):
    encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str).encode()
    return 'record:' + hashlib.sha256(encoded).hexdigest()


class SearchResult(Model):
    source: str
    query: str
    candidates: list[PaperCandidate] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=utcnow)
    error: dict | None = None


class AcademicProvider:
    name: str

    def __init__(self, *, client=None, year_from=None, year_to=None):
        self.client = client
        self.year_from = year_from
        self.year_to = year_to

    async def request(self, url, *, params=None, headers=None):
        async def fetch(client):
            response = await client.get(url, params=params, headers=headers, timeout=30, follow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 8 * 1024 * 1024:
                raise ValueError('Provider response exceeds size limit')
            return response
        if self.client is not None:
            return await fetch(self.client)
        async with httpx.AsyncClient() as client:
            return await fetch(client)

    def failure(self, query, exc):
        # Do not persist exception URLs: some providers send API keys in query parameters.
        if isinstance(exc, httpx.HTTPStatusError):
            error = {'code': 'provider_http_error', 'status': exc.response.status_code}
        else:
            error = {'code': 'provider_response_error', 'message': type(exc).__name__}
        return SearchResult(source=self.name, query=query, error=error)
