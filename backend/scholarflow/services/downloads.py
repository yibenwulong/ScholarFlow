from urllib.parse import urljoin

import httpx

from ..domain.errors import ScholarError


async def download_pdf(url, *, max_bytes=52428800, client=None, validator=None, max_redirects=5):
    if validator is None:
        from gpt_researcher.utils.url_security import validate_url
        validator = lambda value: validate_url(value, allow_private=False)

    async def fetch(active_client):
        current = url
        for redirect in range(max_redirects + 1):
            try:
                validator(current)
            except ValueError as exc:
                raise ScholarError(422, 'unsafe_url', 'The download URL is not a permitted public HTTP address') from exc
            async with active_client.stream('GET', current, follow_redirects=False, timeout=30) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get('location')
                    if not location or redirect == max_redirects:
                        raise ScholarError(422, 'redirect_limit', 'The PDF download exceeded its redirect limit')
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    raise ScholarError(502, 'download_failed', 'The source did not return an accessible PDF',
                                       retryable=response.status_code in {429, 500, 502, 503, 504},
                                       details={'status': response.status_code})
                pieces, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ScholarError(413, 'file_too_large', 'Downloaded PDF exceeds the configured size limit')
                    pieces.append(chunk)
                content = b''.join(pieces)
                if not content.startswith(b'%PDF-'):
                    raise ScholarError(422, 'not_pdf', 'The source returned a non-PDF response')
                return content
        raise ScholarError(422, 'redirect_limit', 'The PDF download exceeded its redirect limit')

    try:
        if client is not None:
            return await fetch(client)
        async with httpx.AsyncClient(headers={'User-Agent': 'ScholarFlow/0.1'}) as active:
            return await fetch(active)
    except httpx.RequestError as exc:
        raise ScholarError(502, 'download_failed', 'The PDF download request failed', retryable=True) from exc
