"""Run one durable worker against the same data directory as the API."""
import argparse
import asyncio

from ..api.dependencies import ScholarServices
from ..config import ScholarSettings
from ..workflows.graph import WorkflowRuntime
from .runner import Worker


async def main(once=False):
    from dotenv import load_dotenv
    load_dotenv()
    services = ScholarServices.create(ScholarSettings.from_env())
    async with WorkflowRuntime(services) as runtime:
        worker = Worker(services.runs.store, driver=runtime.driver,
            lease_seconds=services.settings.lease_seconds, heartbeat_seconds=services.settings.heartbeat_seconds)
        if once:
            await worker.run_once()
        else:
            await worker.run_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ScholarFlow durable research worker')
    parser.add_argument('--once', action='store_true', help='Process at most one queued run and exit')
    args = parser.parse_args()
    try:
        asyncio.run(main(args.once))
    except KeyboardInterrupt:
        pass
