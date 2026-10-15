import asyncio
from uuid import uuid4

from ..domain.errors import ScholarError
from ..persistence.run_store import fingerprint


async def run_with_lease(store, lease, operation, *, heartbeat_seconds=15):
    async def renew():
        while True:
            await asyncio.sleep(heartbeat_seconds)
            if not store.heartbeat(lease):
                raise ScholarError(409, 'lease_lost', 'This task is no longer owned by this worker')

    action = asyncio.create_task(operation())
    heartbeat = asyncio.create_task(renew())
    try:
        finished, _ = await asyncio.wait({action, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in finished:
            await heartbeat
        return await action
    finally:
        action.cancel()
        heartbeat.cancel()
        await asyncio.gather(action, heartbeat, return_exceptions=True)


class ArtifactExecutor:
    def __init__(self, store, *, owner=None, concurrency=2, lease_seconds=60, heartbeat_seconds=15, sleep=asyncio.sleep):
        self.store = store
        self.owner = owner or str(uuid4())
        self.semaphore = asyncio.Semaphore(concurrency)
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.sleep = sleep

    async def execute(self, run_id, stage, manifest, operation):
        run = self.store.get_run(run_id)
        scoped = dict(manifest, _project_id=run.project_id)
        job = self.store.enqueue(run_id, 'artifact', stage, scoped, 'artifact:' + fingerprint(scoped))
        if job.status == 'succeeded':
            return job.result
        cached = self.store.cached_artifact(stage, job.input_hash, run.project_id)
        async with self.semaphore:
            for attempt in range(3):
                lease = self.store.claim_artifact(job.id, self.owner, ttl_seconds=self.lease_seconds)
                if lease is None:
                    current = self.store.get_job(job.id)
                    if current.status == 'succeeded':
                        return current.result
                    raise ScholarError(409, 'task_busy', 'This task is already owned by another execution', retryable=True)
                try:
                    value = cached.result if cached else await run_with_lease(
                        self.store, lease, operation, heartbeat_seconds=self.heartbeat_seconds)
                    if not self.store.finish(lease, value):
                        raise ScholarError(409, 'lease_lost', 'The task result arrived after cancellation or takeover')
                    self.store.append_event(run_id, 'artifact_complete', stage, {
                        'job_id': job.id, 'reused_from': cached.id if cached else None})
                    return value
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    retry = isinstance(exc, ScholarError) and exc.retryable and attempt < 2
                    details = {'code': exc.code if isinstance(exc, ScholarError) else 'operation_failed',
                               'message': exc.message if isinstance(exc, ScholarError) else type(exc).__name__}
                    self.store.fail(lease, details, retry=retry)
                    if not retry:
                        raise
                    await self.sleep(2 ** attempt)
        raise RuntimeError('Artifact did not produce a result')
