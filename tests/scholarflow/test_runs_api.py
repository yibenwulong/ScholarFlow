import pytest


@pytest.mark.asyncio
async def test_runs_return_202_reuse_keys_and_support_cancel_and_event_cursor(api):
    project = (await api.post('/api/v1/projects', json={'question': 'A literature question'})).json()
    url = f"/api/v1/projects/{project['id']}/runs"
    headers = {'Idempotency-Key': 'start-one'}
    response = await api.post(url, json={'start_at': 'plan'}, headers=headers)
    assert response.status_code == 202
    run = response.json()
    assert (await api.post(url, json={'start_at': 'plan'}, headers=headers)).json()['id'] == run['id']
    assert (await api.post(url, json={'start_at': 'search'}, headers=headers)).status_code == 409
    jobs = (await api.get(f"/api/v1/runs/{run['id']}/jobs")).json()
    assert len(jobs['items']) == 1
    cancelled = await api.post(f"/api/v1/runs/{run['id']}/actions", json={
        'action': 'cancel', 'expected_revision': run['revision']}, headers={'Idempotency-Key': 'cancel'})
    assert cancelled.status_code == 202
    assert cancelled.json()['status'] == 'cancelled'
    events = (await api.get(f"/api/v1/runs/{run['id']}/events?after=1")).json()['items']
    assert [event['seq'] for event in events] == [2]
