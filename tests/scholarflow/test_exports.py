from io import BytesIO
from zipfile import ZipFile

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.services.exports import ExportService
from .test_report_versions import report_context
from .test_extractions import extraction_context
from .test_documents import docs
from .test_library import library


@pytest.mark.asyncio
async def test_three_exports_use_same_saved_version_and_reject_unaccepted_issues(report_context, settings):
    reports, version, _, _ = report_context
    check = await reports.check(version.id)
    service = ExportService(reports.db, settings)
    with pytest.raises(ScholarError):
        service.request(version.id, 'markdown', check.id, None, 'blocked')
    accepted = reports.accept_draft(version.id, check.id)
    artifacts = {}
    for format in ('markdown', 'docx', 'bibtex'):
        job = service.request(version.id, format, check.id, accepted.id, 'export-' + format)
        assert service.request(version.id, format, check.id, accepted.id, 'export-' + format).id == job.id
        assert job.document_version_ids == version.manifest['document_version_ids']
        artifact = service.build(job.id)
        artifacts[format] = service.resolve(artifact.storage_key, format).read_bytes()
        assert artifact.report_version_id == version.id
    markdown = artifacts['markdown'].decode()
    assert 'DRAFT' in markdown and version.id in markdown
    assert '[1]' in markdown
    with ZipFile(BytesIO(artifacts['docx'])) as archive:
        xml = archive.read('word/document.xml').decode()
        assert 'DRAFT' in xml and version.id in xml and '[1]' in xml
        assert 'Synthetic paper' in xml
    bibtex = artifacts['bibtex'].decode()
    assert '@article{sf_fixture,' in bibtex
    assert bibtex.count('@article{') == 1


@pytest.mark.asyncio
async def test_export_cannot_reuse_acceptance_for_another_verification(report_context, settings):
    reports, version, _, _ = report_context
    first = await reports.check(version.id)
    accepted = reports.accept_draft(version.id, first.id)
    later = await reports.check(version.id)
    with pytest.raises(ScholarError):
        ExportService(reports.db, settings).request(version.id, 'docx', later.id, accepted.id, 'wrong-check')


@pytest.mark.asyncio
async def test_export_worker_publishes_file_through_controlled_endpoint(api, report_context):
    from backend.scholarflow.persistence.run_store import RunStore
    from backend.scholarflow.worker.runner import Worker
    from backend.scholarflow.workflows.graph import WorkflowRuntime
    reports, version, _, _ = report_context
    RunStore(reports.db).put_manifest(version.run_id, 'report', {'report_version_id': version.id}, generation=1)
    check = await reports.check(version.id)
    acceptance = reports.accept_draft(version.id, check.id)
    RunStore(reports.db).put_manifest(version.run_id, 'verification', {'verification_record_id': check.id}, generation=1)
    response = await api.post(f'/api/v1/reports/{version.id}/exports', json={
        'format': 'docx', 'verification_id': check.id, 'draft_acceptance_id': acceptance.id}, headers={'Idempotency-Key': 'word'})
    assert response.status_code == 202
    export_id = response.json()['export']['id']
    assert (await api.get(f'/api/v1/exports/{export_id}/file')).status_code == 404
    services = api._transport.app.state.scholarflow
    async with WorkflowRuntime(services) as runtime:
        assert await Worker(services.runs.store, driver=runtime.driver).run_once()
    assert services.runs.get(response.json()['run_id']).status == 'completed'
    file = await api.get(f'/api/v1/exports/{export_id}/file')
    assert file.status_code == 200
    assert file.content.startswith(b'PK')
