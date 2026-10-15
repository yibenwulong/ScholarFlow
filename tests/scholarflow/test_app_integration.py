def test_legacy_app_keeps_reports_and_websocket_routes(monkeypatch, settings):
    monkeypatch.setenv('SCHOLARFLOW_DATA_DIR', str(settings.data_dir))
    from backend.server.app import app
    paths = {getattr(route, 'path', None) for route in app.routes}
    assert {'/api/reports', '/report/', '/ws'} <= paths
    assert '/api/v1/projects' in app.openapi()['paths']
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        assert client.get('/api/v1/projects').status_code == 200
        assert 'detail' in client.post('/report/', json={}).json()
        with client.websocket_connect('/api/v1/runs/unknown/ws') as socket:
            assert socket.receive_json()['error']['code'] == 'not_found'
