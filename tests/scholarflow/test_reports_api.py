import pytest

from .test_report_versions import report_context
from .test_extractions import extraction_context
from .test_documents import docs, make_pdf
from .test_library import library


@pytest.mark.asyncio
async def test_reports_versions_checks_and_project_ownership(api, report_context):
    service, version, _, _ = report_context
    from backend.scholarflow.persistence.run_store import RunStore
    RunStore(service.db).put_manifest(version.run_id, 'report', {'report_version_id': version.id}, generation=1)
    response = await api.get(f'/api/v1/reports/{version.id}')
    assert response.status_code == 200
    assert response.json()['version']['id'] == version.id
    assert len(response.json()['citations']) == 2
    other = (await api.post('/api/v1/projects', json={'question': 'Other'})).json()
    draft = version.model_dump(mode='json', exclude={'id', 'revision', 'created_at', 'project_id', 'previous_id'})
    invalid = await api.post(f"/api/v1/projects/{other['id']}/reports", json={'expected_revision': 0, 'draft': draft})
    assert invalid.status_code == 422
    check = await api.post(f'/api/v1/reports/{version.id}/checks', json={}, headers={'Idempotency-Key': 'check'})
    assert check.status_code == 202
    assert check.json()['kind'] == 'report_check'


@pytest.mark.asyncio
async def test_unpublished_ai_rows_do_not_enter_canonical_matrix_or_reports(api, report_context):
    _, version, _, paper = report_context
    assert (await api.get(f'/api/v1/projects/{version.project_id}/reports')).json()['items'] == []
    assert (await api.get(f'/api/v1/projects/{version.project_id}/extractions/{paper.paper_id}')).status_code == 404


@pytest.mark.asyncio
async def test_pdf_import_is_async_and_enforces_project_membership(api, docs):
    _, project, paper, _ = docs
    response = await api.post(f'/api/v1/projects/{project.id}/imports',
        data={'paper_version_id': paper.paper_version_id}, files={'file': ('paper.pdf', make_pdf(), 'application/pdf')},
        headers={'Idempotency-Key': 'upload'})
    assert response.status_code == 202
    assert response.json()['run_id']
    other = (await api.post('/api/v1/projects', json={'question': 'Other'})).json()
    denied = await api.post(f"/api/v1/projects/{other['id']}/imports",
        data={'paper_version_id': paper.paper_version_id}, files={'file': ('paper.pdf', make_pdf(), 'application/pdf')},
        headers={'Idempotency-Key': 'other'})
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_import_worker_publishes_parsed_document_and_reuses_operation_id(api, docs):
    from backend.scholarflow.worker.runner import Worker
    from backend.scholarflow.workflows.graph import WorkflowRuntime
    _, project, paper, _ = docs
    content = make_pdf()
    async def upload():
        return await api.post(f'/api/v1/projects/{project.id}/imports',
            data={'paper_version_id': paper.paper_version_id}, files={'file': ('paper.pdf', content, 'application/pdf')},
            headers={'Idempotency-Key': 'pdf-import'})
    first, repeated = await upload(), await upload()
    assert first.status_code == repeated.status_code == 202
    assert first.json()['import_id'] == repeated.json()['import_id']
    services = api._transport.app.state.scholarflow
    async with WorkflowRuntime(services) as runtime:
        assert await Worker(services.runs.store, driver=runtime.driver).run_once()
    imported = (await api.get('/api/v1/imports/' + first.json()['import_id'])).json()
    assert imported['status'] == 'ready'
    document = (await api.get('/api/v1/documents/' + imported['result']['document_version_ids'][0])).json()
    assert document['parse_status'] == 'ready'


@pytest.mark.asyncio
async def test_evidence_review_is_audited_and_used_for_the_original_claim(api, report_context):
    from backend.scholarflow.domain.evidence import ClaimRevision, Evidence, Passage
    from backend.scholarflow.services.verification import Verifier
    reports, version, _, _ = report_context
    occurrence = version.occurrences[0]
    evidence_id = occurrence.evidence_ids[0]
    response = await api.post(f'/api/v1/evidence/{evidence_id}/reviews', json={
        'expected_revision': 0, 'verdict': 'supported', 'reason': 'Checked the quoted source directly'})
    assert response.status_code == 201
    evidence = reports.records.get('evidence', evidence_id, Evidence)
    passage = reports.records.get('passage', evidence.passage_id, Passage)
    claim = reports.records.get('claim', evidence.claim_revision_id, ClaimRevision)
    check = await Verifier(db=reports.db).verify_claim(claim, [evidence], [passage])
    assert check.status == 'supported'
    assert check.checker == 'human_review_v1'
    edited = claim.model_copy(update={'text': 'The method does not use a graph.'})
    assert (await Verifier(db=reports.db).verify_claim(edited, [evidence], [passage])).status == 'needs_recheck'


@pytest.mark.asyncio
async def test_late_unpublished_verification_cannot_replace_canonical_quality(api, report_context):
    from backend.scholarflow.domain.reports import VerificationRecord
    from backend.scholarflow.persistence.run_store import RunStore
    reports, version, _, _ = report_context
    check = await reports.check(version.id)
    store = RunStore(reports.db)
    store.put_manifest(version.run_id, 'final_report', {
        'report_version_id': version.id, 'verification_record_id': check.id}, generation=1)
    orphan = VerificationRecord(project_id=version.project_id, report_version_id=version.id,
                                checker='report_support_v1', quality_status='passed', checks=[])
    reports.records.insert('verification', orphan, project_id=version.project_id, links=[('report', version.id)])
    response = await api.get(f'/api/v1/reports/{version.id}')
    assert response.json()['verification']['id'] == check.id


@pytest.mark.asyncio
async def test_orphan_versions_do_not_block_edits_of_the_published_report(api, report_context):
    from backend.scholarflow.domain.reports import ReportDraft
    from backend.scholarflow.persistence.run_store import RunStore
    reports, version, _, _ = report_context
    store = RunStore(reports.db)
    store.put_manifest(version.run_id, 'report', {'report_version_id': version.id}, generation=1)
    draft = ReportDraft(**version.model_dump(exclude={'id', 'revision', 'created_at', 'project_id', 'previous_id'}))
    orphan = reports.save(version.project_id, draft, version.revision)
    draft.blocks[0].text = 'Human revision. [@sf_fixture]'
    saved = await api.post(f'/api/v1/projects/{version.project_id}/reports', json={
        'expected_revision': version.revision, 'draft': draft.model_dump(mode='json')})
    assert saved.status_code == 201
    assert saved.json()['previous_id'] == version.id
    assert saved.json()['revision'] > orphan.revision
    listed = (await api.get(f'/api/v1/projects/{version.project_id}/reports')).json()['items']
    assert {row['id'] for row in listed} == {saved.json()['id'], version.id}


@pytest.mark.asyncio
async def test_auxiliary_check_can_accept_an_edited_issue_bearing_draft(api, report_context):
    from backend.scholarflow.persistence.run_store import RunStore
    from backend.scholarflow.worker.runner import Worker
    from backend.scholarflow.workflows.graph import WorkflowRuntime
    reports, version, _, _ = report_context
    RunStore(reports.db).put_manifest(version.run_id, 'report', {'report_version_id': version.id}, generation=1)
    services = api._transport.app.state.scholarflow
    services.reports.verifier = reports.verifier
    check = await api.post(f'/api/v1/reports/{version.id}/checks', headers={'Idempotency-Key': 'recheck'})
    async with WorkflowRuntime(services) as runtime:
        await Worker(services.runs.store, driver=runtime.driver).run_once()
    run = services.runs.get(check.json()['id'])
    assert run.status == 'needs_review'
    response = await api.post(f'/api/v1/runs/{run.id}/actions', json={
        'action': 'accept_draft', 'expected_revision': run.revision, 'report_version_id': version.id,
        'verification_record_id': run.pending_action['verification_record_id']}, headers={'Idempotency-Key': 'accept-check'})
    assert response.status_code == 202
    assert response.json()['status'] == 'completed'


@pytest.mark.asyncio
async def test_matrix_edit_rejects_a_changed_paper_version(api, report_context):
    from backend.scholarflow.persistence.run_store import RunStore
    reports, version, _, paper = report_context
    RunStore(reports.db).put_manifest(version.run_id, 'extractions', {'extraction_ids': version.manifest['extraction_ids']}, generation=1)
    response = await api.patch(f'/api/v1/projects/{version.project_id}/extractions/{paper.paper_id}', json={
        'expected_revision': 1, 'paper_version_id': 'different-paper-version', 'changes': {'method': 'A correction'}})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'paper_version_changed'
