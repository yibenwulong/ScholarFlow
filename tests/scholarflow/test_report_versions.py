import pytest

from backend.scholarflow.domain.reports import CitationBinding, ReportBlock, ReportDraft
from backend.scholarflow.services.reports import ReportService
from backend.scholarflow.services.verification import ClaimCheck
from .test_extractions import extraction_context, FakeModel
from .test_documents import docs
from .test_library import library


class SupportVerifier:
    async def verify_claim(self, claim, evidence, passages):
        return ClaimCheck(status='conflicting' if 'does not' in claim.text else 'supported',
                          reason='Fixture semantic assessment', checker='fixture')


@pytest.fixture
async def report_context(extraction_context):
    from backend.scholarflow.services.extractions import ExtractionService
    documents, run, paper, document = extraction_context
    extractions = ExtractionService(documents.db, FakeModel())
    extraction = await extractions.extract(run.id, paper.paper_version_id, [document.id])
    field = extraction.fields['method']
    first, second = 'The method uses a graph.', 'The method does not use a graph.'
    def block(block_id, text):
        return ReportBlock(id=block_id, text=text + ' [@sf_fixture]', claims=[{
            'start': 0, 'end': len(text), 'token_index': 0,
            'claim_revision_ids': field.claim_ids, 'evidence_ids': field.evidence_ids}])
    draft = ReportDraft(run_id=run.id, title='Fixture review', blocks=[block('first', first), block('second', second)],
        bindings=[CitationBinding(key='sf_fixture', paper_version_id=paper.paper_version_id,
                                  bibliography_snapshot={'title': 'Synthetic paper', 'authors': [], 'year': None})],
        manifest={'extraction_ids': [extraction.id], 'document_version_ids': [document.id]})
    service = ReportService(documents.db, FakeModel(), verifier=SupportVerifier())
    version = service.save(run.project_id, draft, 0)
    return service, version, extractions, paper


@pytest.mark.asyncio
async def test_edited_reports_are_immutable_and_matrix_changes_mark_projection_outdated(report_context):
    service, original, extractions, paper = report_context
    blocks = [b.model_copy(deep=True) for b in original.blocks]
    draft = ReportDraft(**original.model_dump(exclude={'id', 'revision', 'created_at', 'project_id', 'previous_id'}))
    draft.blocks[0] = draft.blocks[0].model_copy(update={'text': 'A human edited sentence. [@sf_fixture]'})
    edited = service.save(original.project_id, draft, original.revision)
    assert edited.previous_id == original.id
    assert service.get(original.id).blocks == blocks
    assert not service.store.is_outdated(original.id)
    extractions.edit(original.project_id, paper.paper_id, {'method': 'Correction'}, 1)
    assert service.store.is_outdated(original.id)
    assert service.get(original.id).blocks == blocks


@pytest.mark.asyncio
async def test_edited_citation_can_be_explicitly_rebound_and_rechecked(api, report_context):
    from backend.scholarflow.persistence.run_store import RunStore
    service, version, _, _ = report_context
    RunStore(service.db).put_manifest(version.run_id, 'report', {'report_version_id': version.id}, generation=1)
    options = await api.get(f'/api/v1/reports/{version.id}/citation-options')
    assert options.status_code == 200
    assert options.json()['items'][0]['evidence_id'] == version.occurrences[0].evidence_ids[0]
    draft = ReportDraft.model_validate(version.model_dump(include=set(ReportDraft.model_fields)))
    draft.blocks[0] = draft.blocks[0].model_copy(update={'text': version.blocks[0].text.replace('graph.', 'graph!')})
    response = await api.post(f'/api/v1/projects/{version.project_id}/reports', json={
        'expected_revision': version.revision, 'draft': draft.model_dump(mode='json'), 'confirmed_binding_blocks': ['first']})
    assert response.status_code == 201
    saved = service.get(response.json()['id'])
    assert saved.occurrences[0].evidence_ids == version.occurrences[0].evidence_ids
    check = await service.check(saved.id)
    assert check.checks[0]['status'] == 'supported'
