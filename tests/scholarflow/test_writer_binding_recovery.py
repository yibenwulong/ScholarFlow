"""An invalid generated locator must not discard an otherwise readable draft."""
import json

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.services.extractions import ExtractionService
from backend.scholarflow.services.reports import ReportService
from .test_extractions import FakeModel, extraction_context
from .test_documents import docs
from .test_library import library


async def writer_context(extraction_context, error):
    documents, run, paper, document = extraction_context
    extraction = await ExtractionService(documents.db, FakeModel()).extract(
        run.id, paper.paper_version_id, [document.id])
    field = extraction.fields['method']
    key = 'sf_' + paper.paper_version_id.replace('-', '')
    statement = 'The method uses a graph.'
    claim = {'start': 0, 'end': len(statement), 'token_index': 0,
             'claim_revision_ids': field.claim_ids, 'evidence_ids': field.evidence_ids}
    claims = [claim]
    if error == 'index':
        claims = [dict(claim, token_index=1)]
    elif error == 'duplicate':
        claims = [claim, dict(claim)]
    elif error == 'span':
        claims = [dict(claim, end=len(statement) + 10)]
    elif error == 'unknown_key':
        key = 'sf_not_an_input'
    output = {'title': 'Draft from extracted evidence', 'blocks': [{
        'id': 'method', 'heading': 'Method', 'text': statement + f' [@{key}]', 'claims': claims}]}
    model = FakeModel(json.dumps(output))
    service = ReportService(documents.db, model)
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    return service, draft, model, run


@pytest.mark.asyncio
@pytest.mark.parametrize('error', ['index', 'duplicate', 'span'])
async def test_generated_bad_binding_is_a_visible_unverified_draft(extraction_context, error):
    service, draft, model, run = await writer_context(extraction_context, error)
    saved = service.save(run.project_id, draft, 0)
    assert saved.blocks[0].text == draft.blocks[0].text
    assert len(saved.occurrences) == 1
    assert saved.occurrences[0].claim_revision_ids == []
    assert saved.occurrences[0].evidence_ids == []
    assert saved.manifest['writer_issues'][0]['block_id'] == 'method'
    assert saved.manifest['writer_issues'][0]['code'] in {'invalid_claim_binding', 'invalid_claim_span'}
    checked = await service.check(saved.id)
    assert checked.quality_status == 'issues_present'
    assert any(item['status'] == 'needs_recheck' for item in checked.checks)
    # Creating a reviewable draft does not invent evidence or ask another model to repair it.
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_valid_generated_binding_is_retained(extraction_context):
    service, draft, _, run = await writer_context(extraction_context, None)
    saved = service.save(run.project_id, draft, 0)
    assert saved.occurrences[0].claim_revision_ids
    assert saved.occurrences[0].evidence_ids
    assert saved.manifest.get('writer_issues', []) == []


@pytest.mark.asyncio
async def test_unknown_generated_citation_is_still_rejected(extraction_context):
    service, draft, _, run = await writer_context(extraction_context, 'unknown_key')
    with pytest.raises(ScholarError) as failure:
        service.save(run.project_id, draft, 0)
    assert failure.value.code == 'unknown_citation_key'
