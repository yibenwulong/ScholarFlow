"""A bad paper must not discard independent successful reading work."""
import asyncio
import json

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.evidence import Passage
from backend.scholarflow.domain.library import PaperCandidate
from backend.scholarflow.domain.projects import PlanDraft
from backend.scholarflow.domain.runs import RunCreate
from backend.scholarflow.workflows.nodes import ResearchNodes
from .test_workflow_gates import workflow_context


async def prepared_reading(context):
    services, model, provider, project = context
    candidates = (await provider.search('fixture')).candidates + [PaperCandidate(
        title='Paper 2', source='arxiv', source_id='2601.00002v1',
        identifiers={'arxiv': '2601.00002v1'}, abstract='The method uses a graph.')]
    papers = services.library.import_candidates(project.id, candidates)
    selection = services.library.freeze_selection(project.id, [p.paper_version_id for p in papers], 0)
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    run = services.runs.create(project.id, RunCreate(start_at='analyze', plan_revision_id=plan.id,
        selection_id=selection.id), 'start')
    nodes = ResearchNodes(services, providers={'arxiv': provider})
    state = {'run_id': run.id, 'generation': run.execution_generation, 'stage': 'documents'}
    await nodes.documents(state)
    return services, model, nodes, state, papers


def paper_for_request(services, request):
    passage_id = json.loads(request.messages[-1]['content'])['passages'][0]['passage_id']
    passage = services.extractions.records.get('passage', passage_id, Passage)
    return services.documents.get(passage.document_version_id).paper_version_id


@pytest.mark.asyncio
async def test_bad_paper_does_not_cancel_remaining_papers_or_start_writer(workflow_context):
    services, model, nodes, state, papers = await prepared_reading(workflow_context)
    original, calls = model.complete, []
    bad_id = papers[0].paper_version_id
    async def reader(request):
        if request.purpose == 'read_paper':
            paper_id = paper_for_request(services, request)
            calls.append(paper_id)
            if paper_id == bad_id:
                raise ScholarError(422, 'invalid_model_output', 'Reader returned invalid structured fields')
        return await original(request)
    model.complete = reader
    assert await nodes.read(state) == {'stage': 'stop'}
    run = services.runs.get(state['run_id'])
    assert run.status == 'needs_review'
    assert run.pending_action == {'type': 'paper_read_failures', 'failed_paper_version_ids': [bad_id]}
    assert sorted(calls) == sorted(p.paper_version_id for p in papers)
    readings = [j for j in services.runs.store.list_jobs(run.id) if j.stage == 'read_paper']
    assert sorted(j.status for j in readings) == ['failed', 'succeeded', 'succeeded']
    assert 'extractions' not in run.manifests
    assert 'report' not in run.manifests
    assert not any(purpose in model.purposes for purpose in ['write_report', 'verify_claim'])


@pytest.mark.asyncio
async def test_all_invalid_papers_wait_for_paper_retry_instead_of_documents(workflow_context):
    services, model, nodes, state, papers = await prepared_reading(workflow_context)
    async def reader(_):
        raise ScholarError(422, 'invalid_model_output', 'Reader returned invalid structured fields')
    model.complete = reader
    assert await nodes.read(state) == {'stage': 'stop'}
    run = services.runs.get(state['run_id'])
    assert run.status == 'needs_review'
    assert run.pending_action['type'] == 'paper_read_failures'
    assert set(run.pending_action['failed_paper_version_ids']) == {p.paper_version_id for p in papers}
    assert 'extractions' not in run.manifests


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['lease_lost', 'run_cancelled', 'model_budget_exhausted', 'task_busy'])
async def test_reading_control_errors_are_not_swallowed_as_paper_failures(workflow_context, code):
    services, model, nodes, state, _ = await prepared_reading(workflow_context)
    async def reader(_):
        raise ScholarError(409, code, 'Control signal')
    model.complete = reader
    with pytest.raises(ExceptionGroup) as failure:
        await nodes.read(state)
    def contains(error):
        return (isinstance(error, ScholarError) and error.code == code) or (
            isinstance(error, BaseExceptionGroup) and any(contains(child) for child in error.exceptions))
    assert contains(failure.value)
    assert services.runs.get(state['run_id']).pending_action is None


@pytest.mark.asyncio
async def test_cancel_during_reading_cannot_publish_late_success(workflow_context):
    services, model, nodes, state, _ = await prepared_reading(workflow_context)
    original = model.complete
    started, release = asyncio.Event(), asyncio.Event()
    async def reader(request):
        started.set()
        await release.wait()
        return await original(request)
    model.complete = reader
    task = asyncio.create_task(nodes.read(state))
    await asyncio.wait_for(started.wait(), 5)
    services.runs.store.cancel_run(state['run_id'])
    release.set()
    with pytest.raises(ExceptionGroup):
        await task
    run = services.runs.get(state['run_id'])
    assert run.status == 'cancelled'
    assert 'extractions' not in run.manifests
    assert not any(j.stage == 'read_paper' and j.status == 'succeeded'
                   for j in services.runs.store.list_jobs(run.id))
