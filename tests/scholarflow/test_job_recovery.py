import asyncio

from backend.scholarflow.worker.jobs import ArtifactExecutor
from backend.scholarflow.worker.runner import Worker
from .test_jobs import job_context


async def test_worker_recreation_reuses_six_completed_paper_results(job_context):
    store, run = job_context
    initial = ArtifactExecutor(store, owner='first')
    async def result(value):
        return {'value': value}
    for value in range(6):
        await initial.execute(run.id, 'read', {'document_version': str(value)}, lambda v=value: result(v))
    recovered = ArtifactExecutor(store, owner='recovered')
    computed_after_restart = []
    async def compute(value):
        computed_after_restart.append(value)
        return {'value': value}
    values = [await recovered.execute(run.id, 'read', {'document_version': str(value)}, lambda v=value: compute(v))
              for value in range(10)]
    assert values == [{'value': value} for value in range(10)]
    assert computed_after_restart == [6, 7, 8, 9]


async def test_driver_can_run_parallel_artifacts_without_occupying_their_slots(job_context):
    store, run = job_context
    executor = ArtifactExecutor(store, owner='worker', concurrency=2)
    active, maximum = 0, 0
    async def leaf(value):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {'value': value}
    async def driver(lease):
        values = await asyncio.gather(*[executor.execute(run.id, 'read', {'doc': i}, lambda v=i: leaf(v)) for i in range(4)])
        return {'values': values}
    store.enqueue(run.id, 'driver', 'research', {}, 'key')
    worker = Worker(store, driver=driver, owner='worker')
    assert await asyncio.wait_for(worker.run_once(), timeout=5) is True
    assert maximum == 2
    assert await worker.run_once() is False


async def test_transient_artifact_failure_retries_with_auditable_attempts(job_context):
    from backend.scholarflow.domain.errors import ScholarError
    store, run = job_context
    attempts = []
    async def operation():
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise ScholarError(502, 'temporary', 'Temporary source error', retryable=True)
        return {'ok': True}
    async def no_wait(seconds):
        return None
    executor = ArtifactExecutor(store, owner='worker', sleep=no_wait)
    assert await executor.execute(run.id, 'read', {'doc': 'a'}, operation) == {'ok': True}
    assert attempts == [1, 2, 3]
    assert store.list_jobs(run.id)[0].attempt_no == 3


async def test_cache_reuses_same_document_across_runs_but_not_a_new_version(job_context):
    from backend.scholarflow.domain.runs import Run
    store, run = job_context
    executor = ArtifactExecutor(store, owner='worker')
    async def first():
        return {'finding': '84 percent'}
    async def new():
        return {'finding': '90 percent'}
    assert await executor.execute(run.id, 'read', {'doc': 'version-one'}, first) == {'finding': '84 percent'}
    other = Run(project_id=run.project_id)
    store.records.insert('run', other, project_id=other.project_id)
    assert await executor.execute(other.id, 'read', {'doc': 'version-one'}, new) == {'finding': '84 percent'}
    assert await executor.execute(other.id, 'read', {'doc': 'version-two'}, new) == {'finding': '90 percent'}
