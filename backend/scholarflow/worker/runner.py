import asyncio
import logging
from uuid import uuid4

from .jobs import run_with_lease

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, *, driver, owner=None, lease_seconds=60, heartbeat_seconds=15):
        self.store = store
        self.driver = driver
        self.owner = owner or str(uuid4())
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds

    async def run_once(self):
        lease = self.store.claim_next_driver(self.owner, ttl_seconds=self.lease_seconds)
        if lease is None:
            return False
        try:
            result = await run_with_lease(self.store, lease, lambda: self.driver(lease),
                                          heartbeat_seconds=self.heartbeat_seconds)
            if not self.store.finish(lease, result):
                logger.info('Discarded stale driver result for run %s', lease.run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from ..domain.errors import ScholarError
            error = {'code': exc.code if isinstance(exc, ScholarError) else 'worker_error',
                     'message': exc.message if isinstance(exc, ScholarError) else type(exc).__name__}
            if self.store.fail(lease, error):
                self.store.update_run(lease.run_id, {'status': 'failed', 'issues': [error]},
                                      expected_generation=lease.generation)
        return True

    async def run_forever(self):
        while True:
            if not await self.run_once():
                await asyncio.sleep(0.5)
