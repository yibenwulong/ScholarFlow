import httpx
import pytest

from backend.scholarflow.services.providers.semantic_scholar import SemanticScholarProvider, normalize_semantic
from backend.scholarflow.services.providers.openalex import normalize_openalex
from backend.scholarflow.services.providers.arxiv import normalize_arxiv


def test_semantic_metadata_is_preserved_without_open_pdf():
    candidate = normalize_semantic({'paperId': 's2-1', 'title': 'Paper', 'authors': [{'name': 'Alice Example'}],
                                    'year': 2024, 'abstract': 'Abstract', 'isOpenAccess': False,
                                    'externalIds': {'DOI': 'https://doi.org/10.1234/ABC'}})
    assert candidate.authors == ['Alice Example']
    assert candidate.year == 2024
    assert candidate.identifiers['doi'] == '10.1234/abc'
    assert candidate.pdf_url is None


def test_openalex_keeps_identifiers_and_handles_partial_nested_fields():
    candidate = normalize_openalex({'id': 'https://openalex.org/W1', 'title': 'Title',
        'publication_year': 2023, 'doi': 'https://doi.org/10.1000/XYZ',
        'authorships': [{'author': {'display_name': 'B Example'}}, None],
        'abstract_inverted_index': {'result': [1], 'A': [0]}, 'best_oa_location': []})
    assert candidate.abstract == 'A result'
    assert candidate.identifiers['openalex'] == 'W1'
    assert candidate.authors == ['B Example']
    assert candidate.pdf_url is None


def test_arxiv_retains_version_and_author():
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
    <entry><id>http://arxiv.org/abs/2401.12345v2</id><title>A paper</title>
    <author><name>C Example</name></author><published>2024-01-01T00:00:00Z</published>
    <summary>Evidence</summary><link title="pdf" href="https://arxiv.org/pdf/2401.12345v2"/>
    <arxiv:doi>10.1000/PUBLISHED</arxiv:doi></entry></feed>'''
    candidate = normalize_arxiv(xml)[0]
    assert candidate.identifiers['arxiv'] == '2401.12345v2'
    assert candidate.identifiers['doi'] == '10.1000/published'
    assert candidate.authors == ['C Example']
    assert candidate.publication_status == 'preprint'


async def test_provider_distinguishes_rate_limit_from_empty_results():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429))) as client:
        failed = await SemanticScholarProvider(client=client).search('topic', 20)
    assert failed.error['code'] == 'provider_http_error'
    assert failed.error['status'] == 429
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'data': []}))) as client:
        empty = await SemanticScholarProvider(client=client).search('topic', 20)
    assert empty.error is None
    assert empty.candidates == []


async def test_provider_skips_malformed_hits_without_dropping_valid_metadata():
    payload = {'data': [None, 'bad', {'paperId': 's2-1', 'title': 'Good', 'authors': None}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        result = await SemanticScholarProvider(client=client).search('q', 20)
    assert [c.title for c in result.candidates] == ['Good']
