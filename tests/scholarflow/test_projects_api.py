import httpx
import pytest


async def test_project_survives_application_restart(api, settings):
    from backend.scholarflow.api.app import create_app
    created = await api.post('/api/v1/projects', json={'question': '文献比较'})
    assert created.status_code == 201
    project_id = created.json()['id']
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as other:
            response = await other.get('/api/v1/projects')
    assert response.json()['items'][0]['id'] == project_id
    assert response.json()['items'][0]['question'] == '文献比较'


async def test_project_update_uses_compare_and_swap(api):
    project = (await api.post('/api/v1/projects', json={'question': 'Before'})).json()
    url = '/api/v1/projects/' + project['id']
    updated = await api.patch(url, json={'question': 'After', 'expected_revision': 1})
    assert updated.status_code == 200
    assert updated.json()['revision'] == 2
    stale = await api.patch(url, json={'question': 'Lost edit', 'expected_revision': 1})
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'revision_conflict'
    assert (await api.get(url)).json()['question'] == 'After'


@pytest.mark.parametrize('payload', [{'question': ' '}, {'question': 'Q', 'unexpected': True}])
async def test_invalid_project_returns_structured_error(api, payload):
    response = await api.post('/api/v1/projects', json=payload)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'validation_error'


async def test_missing_project_returns_404(api):
    response = await api.get('/api/v1/projects/missing')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'not_found'
