import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.reports import ReportDraft
from .test_report_versions import report_context
from .test_extractions import extraction_context
from .test_documents import docs
from .test_library import library


@pytest.mark.asyncio
async def test_each_occurrence_is_checked_and_acceptance_binds_exact_version(report_context):
    service, version, _, _ = report_context
    record = await service.check(version.id)
    assert record.checks[0]['status'] == 'supported'
    assert record.checks[1]['status'] == 'conflicting'
    assert record.quality_status == 'issues_present'
    assert service.accept_draft(version.id, record.id).report_version_id == version.id
    draft = ReportDraft(**version.model_dump(exclude={'id', 'revision', 'created_at', 'project_id', 'previous_id'}))
    draft.blocks[1] = draft.blocks[1].model_copy(update={'text': 'Changed. [@sf_fixture]'})
    edited = service.save(version.project_id, draft, version.revision)
    projection = service.store.verifications(edited.id)[0]
    assert projection.checks[0]['status'] == 'supported'
    assert projection.checks[1]['status'] == 'needs_recheck'
    with pytest.raises(ScholarError):
        service.accept_draft(edited.id, record.id)
    assert service.store.verifications(version.id)[0].id == record.id


@pytest.mark.asyncio
async def test_uncited_sentence_cannot_hide_next_to_a_valid_citation(report_context):
    service, version, _, _ = report_context
    draft = ReportDraft(**version.model_dump(exclude={'id', 'revision', 'created_at', 'project_id', 'previous_id'}))
    block = draft.blocks[0]
    changed = block.model_copy(update={'text': block.text + ' This is the best system ever.',
        'claims': [dict(block.claims[0], end=block.claims[0]['end'] + 1)]})
    # Explicitly regenerate the binding, so the first statement remains checked.
    draft.blocks[0] = changed
    edited = service.save(version.project_id, draft, version.revision)
    record = await service.check(edited.id)
    assert any(c.get('block_id') == block.id and c['status'] == 'needs_recheck' for c in record.checks)
    assert record.quality_status == 'issues_present'
