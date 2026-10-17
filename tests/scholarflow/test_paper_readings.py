"""Per-paper publication and retries preserve the original Reader inputs."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.scholarflow.api.dependencies import ScholarServices
from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.library import PaperVersion, SourceRecord
from backend.scholarflow.domain.projects import PlanDraft, ProjectCreate
from backend.scholarflow.domain.reports import ReportBlock, ReportVersion, VerificationRecord
from backend.scholarflow.domain.runs import Run, RunAction
from backend.scholarflow.persistence.projections import Published
from backend.scholarflow.persistence.run_store import fingerprint
from backend.scholarflow.services.model_gateway import ModelResponse
from backend.scholarflow.worker.auxiliary import drive_auxiliary
from backend.scholarflow.worker.runner import Worker
from .test_library import candidate


class Reader:
    def __init__(self):
        self.requests = []
        self.error = None
        self.on_complete = None

    async def complete(self, request):
        self.requests.append(request)
        assert request.purpose == 'read_paper', 'A single-paper retry must only invoke Reader'
        if self.error:
            raise self.error
        if self.on_complete:
            self.on_complete()
        fragment = json.loads(request.messages[-1]['content'])['passages'][0]
        return ModelResponse(text=json.dumps({'fields': {'method': [{
            'value': fragment['text'], 'quote': fragment['text'], 'passage_id': fragment['passage_id']}]}}))


def make_run(context, **changes):
    values = dict(project_id=context.project.id, status='running', stage='read', start_at='analyze',
                  request_snapshot=context.snapshot, manifests={'documents': {'papers': [
                      {'paper_version_id': paper.paper_version_id, 'document_version_ids': [document.id]}
                      for paper, document in zip(context.papers, context.documents)]}})
    values.update(changes)
    run = Run(**values)
    context.services.runs.store.records.insert('run', run, project_id=run.project_id)
    return run


def make_job(context, run, index, stage='read_paper'):
    manifest = {'paper_version_id': context.papers[index].paper_version_id,
                'document_ids': [context.documents[index].id], 'snapshot': run.request_snapshot,
                '_project_id': run.project_id}
    return context.services.runs.store.enqueue(run.id, 'artifact', stage, manifest, fingerprint(manifest))


@pytest.fixture
async def reading_context(settings):
    services = ScholarServices.create(settings)
    model = Reader()
    services.configure_models(model)
    project = services.projects.create(ProjectCreate(question='Compare graph methods'))
    papers = services.library.import_candidates(project.id, [candidate('alpha'), candidate('beta')])
    documents = []
    for paper in papers:
        source = next(item for item in services.library.store.records.list('source_record', SourceRecord)
                      if item.paper_version_id == paper.paper_version_id)
        document = services.documents.import_abstract(paper.paper_version_id, 'The method uses a graph.', source.id)
        services.documents.parse(document.id)
        documents.append(document)
    context = SimpleNamespace(services=services, model=model, project=project, papers=papers, documents=documents,
        snapshot={'project': project.model_dump(mode='json'), 'model': {'provider': 'openai', 'model': 'frozen-reader'},
                  'limits': {'max_model_calls': 100, 'search_limit': 20}, 'human_extractions': {}})
    run = make_run(context)
    jobs = [make_job(context, run, index) for index in range(2)]
    first = services.runs.store.claim_artifact(jobs[0].id, 'fixture')
    extraction = await services.extractions.extract(run.id, papers[0].paper_version_id, [documents[0].id])
    assert services.runs.store.finish(first, {'extraction_id': extraction.id})
    second = services.runs.store.claim_artifact(jobs[1].id, 'fixture')
    assert services.runs.store.fail(second, {'code': 'invalid_model_output', 'message': 'Reader returned invalid structured fields'})
    context.run = services.runs.store.cancel_run(run.id)
    context.jobs, context.extraction = jobs, extraction
    model.requests.clear()
    return context


async def retry(api, context, key='retry-one', source_job_id=None, project_id=None):
    return await api.post(f'/api/v1/projects/{project_id or context.project.id}/reading-retries',
        json={'source_job_id': source_job_id or context.jobs[1].id}, headers={'Idempotency-Key': key})


async def status(api, context):
    response = await api.get(f'/api/v1/projects/{context.project.id}/reading-status')
    assert response.status_code == 200
    return {item['paper_version_id']: item for item in response.json()['items']}


async def tick(context):
    async def driver(lease):
        run = context.services.runs.get(lease.run_id)
        result = await drive_auxiliary(context.services, run, lease.generation)
        return {'run_id': result.id, 'status': result.status, 'generation': lease.generation}
    assert await Worker(context.services.runs.store, driver=driver).run_once()


@pytest.mark.asyncio
async def test_cancelled_parent_retains_successful_readings_and_exposes_each_failure(api, reading_context):
    c = reading_context
    rows = await status(api, c)
    first, second = (rows[paper.paper_version_id] for paper in c.papers)
    assert first['status'] == 'succeeded' and first['extraction_id'] == c.extraction.id
    assert first['source_run_id'] == c.run.id and first['source_job_id'] == c.jobs[0].id
    assert first['can_retry'] is False
    assert second['status'] == 'failed' and second['extraction_id'] is None
    assert second['error']['code'] == 'invalid_model_output' and second['can_retry'] is True
    matrix = await api.get(f'/api/v1/projects/{c.project.id}/extractions')
    assert [item['id'] for item in matrix.json()['items']] == [c.extraction.id]
    claim_id = c.extraction.fields['method'].claim_ids[0]
    evidence = await api.get(f'/api/v1/claims/{claim_id}/evidence')
    assert evidence.status_code == 200 and evidence.json()['items'][0]['evidence']['locator_status'] == 'located'
    assert c.model.requests == []


@pytest.mark.asyncio
async def test_retry_only_reads_target_with_frozen_inputs_and_keeps_old_run_unchanged(api, reading_context, monkeypatch):
    c = reading_context
    monkeypatch.setenv('SCHOLARFLOW_MODEL', 'openai:changed-current-model')
    before = c.services.runs.get(c.run.id).model_dump()
    response = await retry(api, c)
    assert response.status_code == 202
    run = Run.model_validate(response.json()['run'])
    assert run.kind == 'paper_read' and run.target_id == c.jobs[1].id
    assert run.request_snapshot['project'] == c.snapshot['project']
    assert run.request_snapshot['model'] == c.snapshot['model']
    assert run.request_snapshot['limits'] == c.snapshot['limits']
    assert run.request_snapshot == c.snapshot
    await tick(c)
    completed = c.services.runs.get(run.id)
    assert completed.status == 'completed' and 'extractions' in completed.manifests
    assert len(c.model.requests) == 1
    fragments = json.loads(c.model.requests[0].messages[-1]['content'])['passages']
    assert {fragment['document_version_id'] for fragment in fragments} == {c.documents[1].id}
    assert c.services.runs.get(c.run.id).model_dump() == before
    assert c.services.runs.store.get_job(c.jobs[0].id).result == {'extraction_id': c.extraction.id}
    assert set(Published(c.services.db).extractions(c.project.id)) == {paper.paper_version_id for paper in c.papers}
    rows = await status(api, c)
    assert rows[c.papers[1].paper_version_id]['source_job_id'] == c.jobs[1].id
    assert rows[c.papers[1].paper_version_id]['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_different_operation_keys_share_active_retry_and_cannot_repeat_success(api, reading_context):
    c = reading_context
    first, second = await asyncio.gather(retry(api, c, 'first'), retry(api, c, 'second'))
    assert first.status_code == second.status_code == 202
    assert first.json()['run']['id'] == second.json()['run']['id']
    rows = await status(api, c)
    failed = rows[c.papers[1].paper_version_id]
    assert failed['active_retry_run_id'] == first.json()['run']['id'] and failed['can_retry'] is False
    await tick(c)
    repeated = await retry(api, c, 'third')
    assert repeated.status_code in {202, 409}
    if repeated.status_code == 202:
        assert repeated.json()['run']['id'] == first.json()['run']['id']
    assert len([run for run in c.services.runs.list(c.project.id) if run.kind == 'paper_read']) == 1
    assert len(c.model.requests) == 1


@pytest.mark.asyncio
async def test_failed_auxiliary_can_retry_again_using_the_original_source_job(api, reading_context):
    c = reading_context
    c.model.error = ScholarError(422, 'invalid_model_output', 'Fixture Reader schema failure')
    response = await retry(api, c, 'first')
    assert response.status_code == 202
    await tick(c)
    first_id = response.json()['run']['id']
    assert c.services.runs.get(first_id).status == 'failed'
    rows = await status(api, c)
    row = rows[c.papers[1].paper_version_id]
    assert row['status'] == 'failed' and row['can_retry'] is True and row['source_job_id'] == c.jobs[1].id
    c.model.error = None
    response = await retry(api, c, 'second')
    assert response.status_code == 202 and response.json()['run']['id'] != first_id
    await tick(c)
    assert c.services.runs.get(response.json()['run']['id']).status == 'completed'
    assert len(c.model.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('sibling_status', ['queued', 'running'])
async def test_generic_retry_cannot_restart_failed_child_beside_an_active_sibling(api, reading_context, sibling_status):
    c = reading_context
    c.model.error = ScholarError(422, 'invalid_model_output', 'Fixture Reader schema failure')
    first = await retry(api, c, 'first-child')
    assert first.status_code == 202
    await tick(c)
    failed = c.services.runs.get(first.json()['run']['id'])
    assert failed.status == 'failed'
    second = await retry(api, c, 'second-child')
    assert second.status_code == 202
    sibling_id = second.json()['run']['id']
    if sibling_status == 'running':
        assert c.services.runs.store.claim_next_driver('fixture').run_id == sibling_id
    before = failed.model_dump()
    job_ids = [job.id for job in c.services.runs.store.list_jobs(failed.id)]
    response = await api.post(f'/api/v1/runs/{failed.id}/actions', json={
        'action': 'retry', 'expected_revision': failed.revision}, headers={'Idempotency-Key': 'generic-retry'})
    assert response.status_code == 409
    assert 'reading-retries' in response.json()['error']['message']
    assert c.services.runs.get(failed.id).model_dump() == before
    assert [job.id for job in c.services.runs.store.list_jobs(failed.id)] == job_ids
    assert c.services.runs.get(sibling_id).status == sibling_status
    assert len(c.model.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['continue', 'accept_draft'])
async def test_paper_read_cannot_resume_or_accept_a_report_from_pending_metadata(api, reading_context, action):
    c = reading_context
    response = await retry(api, c)
    assert response.status_code == 202
    run = c.services.runs.get(response.json()['run']['id'])
    payload = {'action': action}
    if action == 'continue':
        plan = c.services.projects.save_plan(c.project.id, PlanDraft(queries=['graph methods']), 0)
        c.services.projects.approve_plan(c.project.id, plan.id, plan.revision)
        changes = {'status': 'waiting_for_user', 'pending_action': {'type': 'approve_plan'}}
        payload['plan_revision_id'] = plan.id
    else:
        report = ReportVersion(project_id=c.project.id, run_id=c.run.id, title='Existing draft',
                               blocks=[ReportBlock(id='scope', text='Existing draft for review')])
        c.services.runs.store.records.insert('report', report, project_id=c.project.id)
        verification = VerificationRecord(project_id=c.project.id, report_version_id=report.id,
            checker='fixture', quality_status='issues_present', checks=[])
        c.services.runs.store.records.insert('verification', verification, project_id=c.project.id)
        payload.update(report_version_id=report.id, verification_record_id=verification.id)
        changes = {'status': 'needs_review', 'pending_action': {'type': 'review_report',
                   'report_version_id': report.id, 'verification_record_id': verification.id}}
    run = c.services.runs.store.update_run(run.id, changes)
    before = run.model_dump()
    payload['expected_revision'] = run.revision
    response = await api.post(f'/api/v1/runs/{run.id}/actions', json=payload)
    assert response.status_code == 409
    assert c.services.runs.get(run.id).model_dump() == before
    assert c.model.requests == []


@pytest.mark.asyncio
async def test_cancelled_child_is_not_restarted_through_a_new_operation_key(api, reading_context):
    c = reading_context
    response = await retry(api, c)
    assert response.status_code == 202
    run = c.services.runs.get(response.json()['run']['id'])
    c.services.runs.act(run.id, RunAction(action='cancel', expected_revision=run.revision), 'cancel-child')
    row = (await status(api, c))[c.papers[1].paper_version_id]
    assert row['status'] == 'cancelled' and row['can_retry'] is False
    assert (await retry(api, c, 'after-cancel')).status_code == 409
    assert len([item for item in c.services.runs.list(c.project.id) if item.kind == 'paper_read']) == 1
    assert c.model.requests == []


@pytest.mark.asyncio
async def test_retry_operation_key_cannot_change_the_source_task(api, reading_context):
    c = reading_context
    assert (await retry(api, c, 'same-key')).status_code == 202
    changed = await retry(api, c, 'same-key', source_job_id=c.jobs[0].id)
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'idempotency_conflict'
    assert c.model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['succeeded', 'cross_project'])
async def test_generic_run_create_cannot_bypass_reader_retry_guards(api, reading_context, invalid):
    c = reading_context
    project_id = c.project.id if invalid == 'succeeded' else c.services.projects.create(ProjectCreate(question='Other')).id
    job_id = c.jobs[0].id if invalid == 'succeeded' else c.jobs[1].id
    response = await api.post(f'/api/v1/projects/{project_id}/runs', json={'kind': 'paper_read', 'target_id': job_id})
    assert response.status_code in {404, 409}
    assert not [item for item in c.services.runs.list(c.project.id) if item.kind == 'paper_read']
    assert c.model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['succeeded', 'running', 'nonreader', 'cross_project'])
async def test_retry_rejects_nonfailed_reader_targets_without_dispatch(api, reading_context, invalid):
    c = reading_context
    project_id, job_id = c.project.id, c.jobs[1].id
    if invalid == 'succeeded':
        job_id = c.jobs[0].id
    elif invalid == 'running':
        run = make_run(c)
        job = make_job(c, run, 1)
        c.services.runs.store.claim_artifact(job.id, 'fixture')
        job_id = job.id
    elif invalid == 'nonreader':
        run = make_run(c)
        job = make_job(c, run, 1, 'write_report')
        lease = c.services.runs.store.claim_artifact(job.id, 'fixture')
        c.services.runs.store.fail(lease, {'code': 'fixture', 'message': 'Not a Reader'})
        job_id = job.id
    else:
        project_id = c.services.projects.create(ProjectCreate(question='Another project')).id
    response = await retry(api, c, source_job_id=job_id, project_id=project_id)
    assert response.status_code in {404, 409, 422}
    assert not [run for run in c.services.runs.list(c.project.id) if run.kind == 'paper_read']
    assert c.model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict', ['paper_version', 'human_revision'])
async def test_stale_versions_and_manual_work_block_retry_before_model_dispatch(api, reading_context, conflict):
    c = reading_context
    paper = c.papers[1]
    if conflict == 'paper_version':
        old = c.services.library.store.records.get('paper_version', paper.paper_version_id, PaperVersion)
        new = old.model_copy(update={'id': str(uuid4())})
        c.services.library.store.records.insert('paper_version', new, links=[('paper', paper.paper_id)])
        association = c.services.library.association(c.project.id, paper.paper_id)
        c.services.library.store.records.replace('project_paper', association.model_copy(update={'paper_version_id': new.id}),
                                                expected_revision=association.revision)
    else:
        newer = make_run(c)
        extraction = await c.services.extractions.extract(newer.id, paper.paper_version_id, [c.documents[1].id])
        c.services.runs.store.put_manifest(newer.id, 'extractions', {'extraction_ids': [extraction.id]}, generation=1)
        c.services.extractions.edit(c.project.id, paper.paper_id, {'method': 'Human correction'}, extraction.revision,
                                    published_only=True, expected_paper_version_id=paper.paper_version_id)
        c.model.requests.clear()
    rows = await status(api, c)
    row = rows[paper.paper_version_id]
    assert row['can_retry'] is False and row['retry_block_reason']
    assert (await retry(api, c)).status_code == 409
    assert c.model.requests == []
    if conflict == 'human_revision':
        assert Published(c.services.db).extractions(c.project.id)[paper.paper_version_id].fields['method'].value == 'Human correction'


@pytest.mark.asyncio
@pytest.mark.parametrize('orphan', ['unpublished', 'lost_lease'])
async def test_unpublished_or_lost_lease_extractions_do_not_become_visible(reading_context, orphan):
    c = reading_context
    other = make_run(c)
    job = make_job(c, other, 1)
    lease = c.services.runs.store.claim_artifact(job.id, 'fixture')
    extraction = await c.services.extractions.extract(other.id, c.papers[1].paper_version_id, [c.documents[1].id])
    if orphan == 'lost_lease':
        c.services.runs.store.update_run(other.id, {'execution_generation': 2})
        assert c.services.runs.store.finish(lease, {'extraction_id': extraction.id}) is False
    assert set(Published(c.services.db).extractions(c.project.id)) == {c.papers[0].paper_version_id}


@pytest.mark.asyncio
async def test_successful_cached_reader_result_can_reference_another_run(reading_context):
    c = reading_context
    origin = make_run(c)
    extraction = await c.services.extractions.extract(origin.id, c.papers[1].paper_version_id, [c.documents[1].id])
    reuser = make_run(c)
    job = make_job(c, reuser, 1)
    lease = c.services.runs.store.claim_artifact(job.id, 'fixture')
    assert c.services.runs.store.finish(lease, {'extraction_id': extraction.id})
    assert Published(c.services.db).extractions(c.project.id)[c.papers[1].paper_version_id].id == extraction.id


@pytest.mark.asyncio
async def test_retry_reuses_another_runs_success_without_model_calls(api, reading_context):
    c = reading_context
    origin = make_run(c)
    extraction = await c.services.extractions.extract(origin.id, c.papers[1].paper_version_id, [c.documents[1].id])
    job = make_job(c, origin, 1)
    lease = c.services.runs.store.claim_artifact(job.id, 'fixture')
    assert c.services.runs.store.finish(lease, {'extraction_id': extraction.id})
    c.model.requests.clear()
    response = await retry(api, c)
    assert response.status_code == 202
    await tick(c)
    completed = c.services.runs.get(response.json()['run']['id'])
    assert completed.status == 'completed'
    assert completed.manifests['extractions']['extraction_ids'] == [extraction.id]
    assert c.model.requests == []


@pytest.mark.asyncio
async def test_invalid_cached_result_cannot_publish_the_wrong_paper_as_retry_success(api, reading_context):
    c = reading_context
    other = make_run(c)
    job = make_job(c, other, 1)
    lease = c.services.runs.store.claim_artifact(job.id, 'fixture')
    assert c.services.runs.store.finish(lease, {'extraction_id': c.extraction.id})
    response = await retry(api, c)
    assert response.status_code == 202
    await tick(c)
    run = c.services.runs.get(response.json()['run']['id'])
    assert run.status == 'failed' and 'extractions' not in run.manifests
    assert c.model.requests == []
    assert c.papers[1].paper_version_id not in Published(c.services.db).extractions(c.project.id)


@pytest.mark.asyncio
async def test_human_revision_created_while_retry_is_queued_prevents_dispatch(api, reading_context):
    c = reading_context
    response = await retry(api, c)
    assert response.status_code == 202
    newer = make_run(c)
    extraction = await c.services.extractions.extract(newer.id, c.papers[1].paper_version_id, [c.documents[1].id])
    c.services.runs.store.put_manifest(newer.id, 'extractions', {'extraction_ids': [extraction.id]}, generation=1)
    c.services.extractions.edit(c.project.id, c.papers[1].paper_id, {'method': 'Queued-time human correction'},
                                extraction.revision, published_only=True)
    c.model.requests.clear()
    await tick(c)
    assert c.services.runs.get(response.json()['run']['id']).status == 'failed'
    assert c.model.requests == []
    assert Published(c.services.db).extractions(c.project.id)[c.papers[1].paper_version_id].fields['method'].value == 'Queued-time human correction'


@pytest.mark.asyncio
async def test_human_revision_created_during_model_call_keeps_ai_output_unpublished(api, reading_context):
    c = reading_context
    newer = make_run(c)
    extraction = await c.services.extractions.extract(newer.id, c.papers[1].paper_version_id, [c.documents[1].id])
    c.services.runs.store.put_manifest(newer.id, 'extractions', {'extraction_ids': [extraction.id]}, generation=1)
    response = await retry(api, c)
    assert response.status_code == 202
    c.model.on_complete = lambda: c.services.extractions.edit(c.project.id, c.papers[1].paper_id,
        {'method': 'Concurrent human correction'}, extraction.revision, published_only=True)
    c.model.requests.clear()
    await tick(c)
    assert c.services.runs.get(response.json()['run']['id']).status == 'failed'
    assert len(c.model.requests) == 1
    assert Published(c.services.db).extractions(c.project.id)[c.papers[1].paper_version_id].fields['method'].value == 'Concurrent human correction'


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['lease_lost', 'model_budget_exhausted', 'cancelled'])
async def test_auxiliary_control_errors_cannot_publish_a_successful_extraction(api, reading_context, code):
    c = reading_context
    response = await retry(api, c)
    assert response.status_code == 202
    run = c.services.runs.get(response.json()['run']['id'])
    c.model.error = asyncio.CancelledError() if code == 'cancelled' else ScholarError(409, code, 'Control signal')
    with pytest.raises(asyncio.CancelledError if code == 'cancelled' else ScholarError):
        await drive_auxiliary(c.services, run, run.execution_generation)
    current = c.services.runs.get(run.id)
    assert current.status != 'completed' and 'extractions' not in current.manifests
    assert c.papers[1].paper_version_id not in Published(c.services.db).extractions(c.project.id)
