import asyncio
from dataclasses import asdict
import json
from uuid import uuid4

from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect

from ..domain.errors import ScholarError
from ..domain.runs import RunAction, RunCreate
from .projects import page


def operation_key(key):
    if key is not None and (not key.strip() or len(key) > 200):
        raise ScholarError(422, 'invalid_operation_key', 'Idempotency-Key must contain 1–200 characters')
    return key or str(uuid4())


def run_router(services):
    router = APIRouter()

    @router.post('/projects/{project_id}/runs', status_code=202)
    def create_run(project_id: str, data: RunCreate, key: str | None = Header(default=None, alias='Idempotency-Key')):
        return services.runs.create(project_id, data, operation_key(key))

    @router.get('/projects/{project_id}/runs')
    def runs(project_id: str):
        return page(services.runs.list(project_id))

    @router.get('/runs/{run_id}')
    def run(run_id: str):
        return services.runs.get(run_id)

    @router.get('/runs/{run_id}/jobs')
    def jobs(run_id: str):
        with services.db.transaction(write=False) as conn:
            calls = [dict(row) for row in conn.execute('SELECT id,status,provider_request_id,usage_json,created_at FROM model_calls WHERE run_id=? ORDER BY created_at', (run_id,))]
        for call in calls:
            raw = call.pop('usage_json')
            call['usage'] = json.loads(raw) if raw is not None else None
        return dict(page([asdict(job) for job in services.runs.store.list_jobs(run_id)]), model_calls=calls, cost=None)

    @router.post('/runs/{run_id}/actions', status_code=202)
    def act(run_id: str, data: RunAction, key: str | None = Header(default=None, alias='Idempotency-Key')):
        return services.runs.act(run_id, data, operation_key(key))

    @router.get('/runs/{run_id}/events')
    def events(run_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=1000)):
        values = services.runs.store.events(run_id, after)
        return {'items': values[:limit], 'next_cursor': str(values[limit - 1].seq) if len(values) > limit else None}

    @router.websocket('/runs/{run_id}/ws')
    async def socket(websocket: WebSocket, run_id: str, after: int = Query(default=0, ge=0)):
        await websocket.accept()
        try:
            services.runs.get(run_id)
            cursor = after
            while True:
                for event in services.runs.store.events(run_id, cursor):
                    await websocket.send_json(event.model_dump(mode='json'))
                    cursor = event.seq
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        except WebSocketDisconnect:
            return
        except ScholarError as error:
            await websocket.send_json({'error': {'code': error.code, 'message': error.message}})
            await websocket.close(code=4404)

    return router
