"""Old sparse successes can be reread without losing their saved revisions."""
from uuid import uuid4

import pytest

from backend.scholarflow.domain.evidence import ExtractedField, ExtractionRevision
from backend.scholarflow.domain.runs import Run
from backend.scholarflow.persistence.projections import Published
from backend.scholarflow.persistence.run_store import fingerprint
from .test_paper_readings import make_run, reading_context, retry, status, tick


def legacy_reading(c):
    source = c.documents[0]
    document = c.services.documents.import_abstract(c.papers[0].paper_version_id,
        'The method uses a graph. A previous reader missed the named dataset.', source.source_record_id)
    c.services.documents.parse(document.id)
    c.documents[0] = document
    run = make_run(c)
    manifest = {'paper_version_id': c.papers[0].paper_version_id,
        'document_ids': [c.documents[0].id], 'snapshot': run.request_snapshot,
        '_project_id': c.project.id}
    job = c.services.runs.store.enqueue(run.id, 'artifact', 'read_paper', manifest, fingerprint(manifest))
    lease = c.services.runs.store.claim_artifact(job.id, 'old-reader')
    coverage = dict(c.extraction.coverage)
    coverage.pop('reader_version', None)
    old = c.extraction.model_copy(update={'id': str(uuid4()), 'run_id': run.id,
        'revision': c.extraction.revision + 1, 'previous_id': c.extraction.id, 'coverage': coverage,
        'document_version_ids': [document.id],
        'fields': {name: ExtractedField() for name in c.extraction.fields}})
    c.services.runs.store.records.insert('extraction', old, project_id=c.project.id,
        links=[('run', run.id), ('version', old.paper_version_id)])
    assert c.services.runs.store.finish(lease, {'extraction_id': old.id})
    c.services.runs.store.cancel_run(run.id)
    return run, job, old


@pytest.mark.asyncio
async def test_old_sparse_success_is_flagged_for_rereading_while_matrix_stays_visible(api, reading_context):
    c = reading_context
    _, job, old = legacy_reading(c)
    rows = await status(api, c)
    row = rows[c.papers[0].paper_version_id]
    assert row['status'] == 'succeeded'
    assert row.get('needs_reread') is True
    assert row['can_retry'] is True and row['source_job_id'] == job.id
    assert Published(c.services.db).extractions(c.project.id)[old.paper_version_id].id == old.id


@pytest.mark.asyncio
async def test_refresh_uses_new_reader_cache_version_and_preserves_parent_and_old_result(api, reading_context):
    c = reading_context
    parent, job, old = legacy_reading(c)
    before = c.services.runs.get(parent.id).model_dump()
    response = await retry(api, c, source_job_id=job.id)
    assert response.status_code == 202
    run = Run.model_validate(response.json()['run'])
    assert run.request_snapshot == c.snapshot
    assert run.manifests['reading']['reader_version'] == 'reader-v4'
    await tick(c)
    completed = c.services.runs.get(run.id)
    assert completed.status == 'completed'
    assert len(c.model.requests) == 1, 'An old sparse artifact must not satisfy the new reader contract'
    jobs = [item for item in c.services.runs.store.list_jobs(run.id) if item.stage == 'read_paper']
    assert len(jobs) == 1 and jobs[0].input_manifest['reader_version'] == 'reader-v4'
    assert jobs[0].input_hash != job.input_hash
    result = Published(c.services.db).extractions(c.project.id)[old.paper_version_id]
    assert result.id != old.id and result.coverage['reader_version'] == 'reader-v4'
    assert c.services.runs.store.records.get('extraction', old.id, ExtractionRevision).id == old.id
    assert c.services.runs.get(parent.id).model_dump() == before
    rows = await status(api, c)
    assert rows[old.paper_version_id]['needs_reread'] is False
    assert rows[old.paper_version_id]['can_retry'] is False
    again = await retry(api, c, key='refresh-again', source_job_id=job.id)
    assert again.status_code in {202, 409}
    assert len(c.model.requests) == 1


@pytest.mark.asyncio
async def test_legacy_success_with_human_revision_cannot_be_refreshed(api, reading_context):
    c = reading_context
    _, job, old = legacy_reading(c)
    human = c.services.extractions.edit(c.project.id, c.papers[0].paper_id,
        {'method': 'My corrected reading'}, old.revision, published_only=True)
    rows = await status(api, c)
    assert rows[old.paper_version_id]['can_retry'] is False
    response = await retry(api, c, source_job_id=job.id)
    assert response.status_code == 409
    assert Published(c.services.db).extractions(c.project.id)[old.paper_version_id].id == human.id
    assert c.model.requests == []


@pytest.mark.asyncio
async def test_saved_refresh_artifact_can_finish_after_driver_interruption(api, reading_context):
    c = reading_context
    _, source, _ = legacy_reading(c)
    response = await retry(api, c, source_job_id=source.id)
    assert response.status_code == 202
    run = Run.model_validate(response.json()['run'])
    extraction = await c.services.extractions.extract(run.id, c.papers[0].paper_version_id, [c.documents[0].id])
    inputs = dict(source.input_manifest, reader_version='reader-v4')
    job = c.services.runs.store.enqueue(run.id, 'artifact', 'read_paper', inputs, fingerprint(inputs))
    lease = c.services.runs.store.claim_artifact(job.id, 'interrupted-worker')
    assert c.services.runs.store.finish(lease, {'extraction_id': extraction.id})
    c.model.requests.clear()
    await tick(c)
    completed = c.services.runs.get(run.id)
    assert completed.status == 'completed'
    assert completed.manifests['extractions']['extraction_ids'] == [extraction.id]
    assert c.model.requests == []
