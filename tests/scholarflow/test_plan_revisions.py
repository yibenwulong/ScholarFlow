async def test_plan_approval_is_version_specific_and_does_not_mutate_plan(api):
    project = (await api.post('/api/v1/projects', json={'question': 'Research'})).json()
    base = f"/api/v1/projects/{project['id']}/plans"
    first = await api.post(base, json={'queries': ['first query'], 'expected_revision': 0})
    assert first.status_code == 201
    plan = first.json()
    second = await api.post(base, json={'queries': ['second query'], 'expected_revision': 1})
    assert second.status_code == 201
    approved = await api.post(f"{base}/{plan['id']}/approve", json={'expected_revision': 1})
    assert approved.status_code == 200
    assert approved.json()['plan_revision_id'] == plan['id']
    plans = (await api.get(base)).json()['items']
    assert {p['queries'][0] for p in plans} == {'first query', 'second query'}
    assert sum(p['approved'] for p in plans) == 1


async def test_plan_append_and_approval_reject_stale_or_foreign_versions(api):
    a = (await api.post('/api/v1/projects', json={'question': 'A'})).json()
    b = (await api.post('/api/v1/projects', json={'question': 'B'})).json()
    base_a = f"/api/v1/projects/{a['id']}/plans"
    plan = (await api.post(base_a, json={'queries': ['a'], 'expected_revision': 0})).json()
    stale = await api.post(base_a, json={'queries': ['b'], 'expected_revision': 0})
    assert stale.status_code == 409
    foreign = await api.post(f"/api/v1/projects/{b['id']}/plans/{plan['id']}/approve", json={'expected_revision': 1})
    assert foreign.status_code == 404
    wrong_revision = await api.post(f"{base_a}/{plan['id']}/approve", json={'expected_revision': 9})
    assert wrong_revision.status_code == 409


async def test_empty_search_queries_cannot_be_approved(api):
    project = (await api.post('/api/v1/projects', json={'question': 'Q'})).json()
    response = await api.post(f"/api/v1/projects/{project['id']}/plans", json={'queries': ['  '], 'expected_revision': 0})
    assert response.status_code == 422
