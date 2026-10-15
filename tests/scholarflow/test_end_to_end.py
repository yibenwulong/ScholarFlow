from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest

from backend.scholarflow.api.app import create_app
from backend.scholarflow.worker.runner import Worker
from backend.scholarflow.workflows.graph import WorkflowRuntime


@pytest.mark.asyncio
async def test_real_api_demo_full_flow_and_worker_restart(settings):
    app = create_app(replace(settings, demo=True))
    services = app.state.scholarflow
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        project = (await client.post('/api/v1/projects', json={'question': 'Compare synthetic graph collaboration methods', 'sources': ['arxiv']})).json()
        run = (await client.post(f"/api/v1/projects/{project['id']}/runs", json={}, headers={'Idempotency-Key': 'demo'})).json()
        async def tick():
            async with WorkflowRuntime(services) as runtime:
                assert await Worker(services.runs.store, driver=runtime.driver).run_once()
        await tick()
        current = (await client.get('/api/v1/runs/' + run['id'])).json()
        assert current['status'] == 'waiting_for_user'
        plans = (await client.get(f"/api/v1/projects/{project['id']}/plans")).json()['items']
        plan = plans[0]
        await client.post(f"/api/v1/projects/{project['id']}/plans/{plan['id']}/approve", json={'expected_revision': plan['revision']})
        await client.post(f"/api/v1/runs/{run['id']}/actions", json={'action': 'continue', 'expected_revision': current['revision'], 'plan_revision_id': plan['id']})
        await tick()  # a fresh checkpointer and worker resumes the exact approval
        papers = (await client.get(f"/api/v1/projects/{project['id']}/papers")).json()['items']
        assert len(papers) >= 2
        readable = [paper for paper in papers if paper['version']['source_id'] != 'abstract-c']
        selection = (await client.post(f"/api/v1/projects/{project['id']}/selections", json={
            'expected_revision': 0, 'paper_version_ids': [paper['paper_version_id'] for paper in readable[:2]]})).json()
        current = (await client.get('/api/v1/runs/' + run['id'])).json()
        await client.post(f"/api/v1/runs/{run['id']}/actions", json={'action': 'continue', 'expected_revision': current['revision'], 'selection_id': selection['id']})
        await tick()
        current = (await client.get('/api/v1/runs/' + run['id'])).json()
        assert current['status'] == 'completed'
        detail = (await client.get('/api/v1/reports/' + current['manifests']['final_report']['report_version_id'])).json()
        assert detail['verification']['quality_status'] == 'passed'
        assert services.runs.get(run['id']).model_calls > 0
        first = detail['version']
        assert first['manifest']['document_version_ids']
        bundle = (await client.get('/api/v1/claims/' + first['occurrences'][0]['claim_revision_ids'][0] + '/evidence')).json()
        assert bundle['items'][0]['passage']['page_number'] == 2
        assert bundle['items'][0]['document']['kind'] == 'pdf'
        draft = {key: first[key] for key in ('run_id', 'title', 'blocks', 'bindings', 'occurrences', 'manifest')}
        draft['blocks'][0]['text'] = 'A user-edited interpretation. ' + draft['blocks'][0]['text']
        edited = (await client.post(f"/api/v1/projects/{project['id']}/reports", json={'expected_revision': first['revision'], 'draft': draft})).json()
        check_run = (await client.post(f"/api/v1/reports/{edited['id']}/checks", json={})).json()
        await tick()
        review = services.runs.get(check_run['id'])
        assert review.status == 'needs_review'
        acceptance = (await client.post(f'/api/v1/runs/{review.id}/actions', json={
            'action': 'accept_draft', 'expected_revision': review.revision, 'report_version_id': edited['id'],
            'verification_record_id': review.pending_action['verification_record_id']})).json()
        for format in ('markdown', 'docx', 'bibtex'):
            response = await client.post(f"/api/v1/reports/{edited['id']}/exports", json={'format': format,
                'verification_id': review.pending_action['verification_record_id'], 'draft_acceptance_id': acceptance['manifests']['acceptance_id']})
            assert response.status_code == 202
            await tick()
            export_id = response.json()['export']['id']
            file = await client.get(f'/api/v1/exports/{export_id}/file')
            assert file.status_code == 200
            if format == 'docx':
                with ZipFile(BytesIO(file.content)) as archive:
                    assert edited['id'] in archive.read('word/document.xml').decode()
        assert (await client.get('/api/v1/reports/' + first['id'])).json()['version']['blocks'][0]['text'] != edited['blocks'][0]['text']
