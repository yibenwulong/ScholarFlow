import time

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.runs import RunCreate
from backend.scholarflow.workflows.graph import WorkflowRuntime, thread_config
from .test_workflow_gates import workflow_context, tick


@pytest.mark.asyncio
async def test_worker_restart_retains_plan_and_persistent_interrupt(workflow_context):
    services, model, provider, project = workflow_context
    run = services.runs.create(project.id, RunCreate(), 'start')
    await tick(services, provider)
    run = services.runs.get(run.id)
    async with WorkflowRuntime(services, providers={'arxiv': provider}) as runtime:
        checkpoint = await runtime.graph.aget_state(thread_config(run))
        assert checkpoint.next == ('approve_plan',)
        assert checkpoint.values['run_id'] == run.id
    assert model.purposes.count('plan') == 1


@pytest.mark.asyncio
async def test_generation_takeover_isolates_late_old_checkpoint_and_domain_writes(workflow_context):
    services, _, provider, project = workflow_context
    run = services.runs.create(project.id, RunCreate(), 'start')
    store = services.runs.store
    old_lease = store.claim_next_driver('old', now=time.time() - 10, ttl_seconds=1)
    old_run = store.get_run(run.id)
    new_lease = store.claim_next_driver('new')
    assert new_lease.generation == 2
    async with WorkflowRuntime(services, providers={'arxiv': provider}) as runtime:
        await runtime.driver(new_lease)
        current = store.get_run(run.id)
        before = await runtime.graph.aget_state(thread_config(current))
        await runtime.graph.aupdate_state(thread_config(old_run), {'run_id': run.id, 'generation': 1, 'stage': 'late-old-write'}, as_node='plan')
        after = await runtime.graph.aget_state(thread_config(current))
        assert after.values == before.values
        assert after.next == before.next
        with pytest.raises(ScholarError):
            store.put_manifest(run.id, 'stale', {}, generation=old_lease.generation)


def test_cancelled_run_cannot_continue(workflow_context):
    from backend.scholarflow.domain.runs import RunAction
    services, _, _, project = workflow_context
    run = services.runs.create(project.id, RunCreate(), 'start')
    cancelled = services.runs.act(run.id, RunAction(action='cancel', expected_revision=run.revision), 'cancel')
    assert cancelled.status == 'cancelled'
    with pytest.raises(ScholarError):
        services.runs.act(run.id, RunAction(action='continue', expected_revision=cancelled.revision), 'continue')


@pytest.mark.asyncio
async def test_failed_reader_retries_only_unfinished_paper_after_restart(workflow_context):
    from backend.scholarflow.domain.projects import PlanDraft
    from backend.scholarflow.domain.runs import RunAction
    services, model, provider, project = workflow_context
    original = model.complete
    read_calls = []
    async def flaky(request):
        if request.purpose == 'read_paper':
            read_calls.append(request)
            if len(read_calls) == 2:
                raise RuntimeError('Simulated process interruption before persisting second paper')
        return await original(request)
    model.complete = flaky
    papers = services.library.import_candidates(project.id, (await provider.search('fixture')).candidates)
    selection = services.library.freeze_selection(project.id, [p.paper_version_id for p in papers], 0)
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    run = services.runs.create(project.id, RunCreate(start_at='analyze', plan_revision_id=plan.id, selection_id=selection.id), 'start')
    await tick(services, provider)
    failed = services.runs.get(run.id)
    assert failed.status == 'failed'
    assert len([j for j in services.runs.store.list_jobs(run.id) if j.stage == 'read_paper' and j.status == 'succeeded']) == 1
    services.runs.act(run.id, RunAction(action='retry', expected_revision=failed.revision), 'retry')
    await tick(services, provider)
    assert services.runs.get(run.id).status == 'completed'
    assert len(read_calls) == 3


@pytest.mark.asyncio
async def test_transient_source_failure_retries_and_keeps_successful_coverage(workflow_context):
    from backend.scholarflow.domain.projects import PlanDraft
    from backend.scholarflow.services.providers.base import SearchResult
    from backend.scholarflow.workflows.nodes import ResearchNodes
    services, _, provider, project = workflow_context
    original = provider.search
    attempts = 0
    async def flaky(query, limit=20):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return SearchResult(source='arxiv', query=query, error={'code': 'provider_http_error', 'status': 503})
        return await original(query, limit)
    provider.search = flaky
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    run = services.runs.create(project.id, RunCreate(start_at='search', plan_revision_id=plan.id), 'start')
    nodes = ResearchNodes(services, providers={'arxiv': provider})
    async def no_sleep(_): pass
    nodes.executor.sleep = no_sleep
    await nodes.search({'run_id': run.id, 'generation': run.execution_generation, 'stage': 'search'})
    assert attempts == 2
    assert len(services.runs.get(run.id).manifests['search']['paper_version_ids']) == 2
