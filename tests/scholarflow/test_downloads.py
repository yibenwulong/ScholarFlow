import httpx
import pytest

from backend.scholarflow.services.downloads import download_pdf
from backend.scholarflow.domain.errors import ScholarError


async def test_redirect_target_is_validated_before_fetching_it():
    visited = []
    def validate(url):
        visited.append(url)
        if '127.0.0.1' in url:
            raise ValueError('private address')
    def response(request):
        if request.url.host == '127.0.0.1':
            pytest.fail('Private redirect must never be requested')
        return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(ScholarError) as error:
            await download_pdf('https://example.org/paper', client=client, validator=validate)
    assert error.value.code == 'unsafe_url'
    assert visited == ['https://example.org/paper', 'http://127.0.0.1/private']


@pytest.mark.parametrize('content,max_bytes,code', [(b'<html>login</html>', 1000, 'not_pdf'), (b'%PDF-' + b'x' * 100, 20, 'file_too_large')])
async def test_invalid_or_oversized_download_is_rejected(content, max_bytes, code):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as client:
        with pytest.raises(ScholarError) as error:
            await download_pdf('https://example.org/paper', max_bytes=max_bytes, client=client, validator=lambda url: url)
    assert error.value.code == code
