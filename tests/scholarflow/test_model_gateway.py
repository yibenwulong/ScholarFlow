import httpcore
import httpx
import openai
import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.services.model_gateway import ModelGateway, ModelRequest, ModelResponse
from .test_jobs import job_context


@pytest.mark.asyncio
async def test_budget_reserved_before_dispatch_and_unknown_usage_stays_unknown(job_context):
    store, run = job_context
    calls = []
    async def dispatch(request):
        calls.append(request)
        return ModelResponse(text='{}')
    gateway = ModelGateway(store.db, dispatch=dispatch)
    request = ModelRequest(run_id=run.id, purpose='read', messages=[{'role': 'user', 'content': 'fixture'}])
    for _ in range(100):
        assert (await gateway.complete(request)).usage is None
    with pytest.raises(ScholarError) as error:
        await gateway.complete(request)
    assert error.value.code == 'model_budget_exhausted'
    assert len(calls) == 100
    with store.db.transaction(write=False) as conn:
        assert conn.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] == 100


@pytest.mark.asyncio
async def test_failed_dispatch_is_counted_and_errors_do_not_persist_secrets(job_context):
    store, run = job_context
    async def dispatch(request):
        raise TimeoutError('https://service.test?api_key=should-not-persist')
    gateway = ModelGateway(store.db, dispatch=dispatch)
    with pytest.raises(ScholarError) as error:
        await gateway.complete(ModelRequest(run_id=run.id, purpose='read', messages=[]))
    assert error.value.retryable
    assert 'should-not-persist' not in str(error.value)
    assert store.get_run(run.id).model_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('transport_error,retryable', [
    (httpx.RemoteProtocolError, True),
    (httpcore.RemoteProtocolError, True),
    (httpx.LocalProtocolError, False),
    (httpcore.LocalProtocolError, False),
])
async def test_wrapped_transport_failure_preserves_retry_policy_and_safe_audit(job_context, transport_error, retryable):
    store, run = job_context
    url = 'https://model.invalid/v1/chat/completions?api_key=fixture-secret'
    calls = 0
    async def dispatch(request):
        nonlocal calls
        calls += 1
        connection_error = openai.APIConnectionError(request=httpx.Request('POST', url))
        connection_error.__cause__ = transport_error(url)
        raise RuntimeError('Helper request failed') from connection_error
    gateway = ModelGateway(store.db, dispatch=dispatch)
    with pytest.raises(ScholarError) as error:
        await gateway.complete(ModelRequest(run_id=run.id, purpose='write_report', messages=[]))
    assert error.value.code == 'model_request_failed'
    assert error.value.retryable is retryable
    assert calls == 1
    assert store.get_run(run.id).model_calls == 1
    with store.db.transaction(write=False) as conn:
        rows = [dict(row) for row in conn.execute('SELECT * FROM model_calls WHERE run_id=?', (run.id,))]
    assert [row['status'] for row in rows] == ['failed']
    assert 'fixture-secret' not in str(error.value) + str(rows)
    assert 'https://' not in str(error.value) + str(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,retryable', [(400, False), (401, False), (429, True), (500, True), (503, True)])
async def test_wrapped_http_failure_uses_status_before_the_innermost_cause(job_context, status, retryable):
    store, run = job_context
    request = httpx.Request('POST', 'https://model.invalid/?api_key=fixture-secret')
    async def dispatch(_):
        status_error = openai.APIStatusError('fixture-secret', response=httpx.Response(status, request=request), body=None)
        status_error.__cause__ = ValueError('fixture-secret')
        raise RuntimeError('Helper request failed') from status_error
    with pytest.raises(ScholarError) as error:
        await ModelGateway(store.db, dispatch=dispatch).complete(ModelRequest(run_id=run.id, purpose='write_report', messages=[]))
    assert error.value.retryable is retryable
    assert store.get_run(run.id).model_calls == 1
    assert 'fixture-secret' not in str(error.value)
    assert 'https://' not in str(error.value)


@pytest.mark.asyncio
async def test_remote_protocol_retry_recovers_report_job_and_counts_each_dispatch(job_context):
    from backend.scholarflow.worker.jobs import ArtifactExecutor
    store, run = job_context
    calls, failures = 0, []
    async def dispatch(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            connection_error = openai.APIConnectionError(request=httpx.Request('POST', 'https://model.invalid/?api_key=fixture-secret'))
            connection_error.__cause__ = httpx.RemoteProtocolError('fixture-secret')
            raise RuntimeError('Helper request failed') from connection_error
        return ModelResponse(text='recovered report')
    gateway = ModelGateway(store.db, dispatch=dispatch)
    async def operation():
        try:
            response = await gateway.complete(ModelRequest(run_id=run.id, purpose='write_report', messages=[]))
        except ScholarError as error:
            failures.append(str(error))
            raise
        return {'text': response.text}
    async def no_sleep(_):
        pass
    result = await ArtifactExecutor(store, sleep=no_sleep).execute(run.id, 'write_report', {'input': 'fixed'}, operation)
    assert result == {'text': 'recovered report'}
    assert calls == 2
    assert store.get_run(run.id).model_calls == 2
    job, = store.list_jobs(run.id)
    assert job.status == 'succeeded'
    assert job.attempt_no == 2
    with store.db.transaction(write=False) as conn:
        rows = conn.execute('SELECT status FROM model_calls WHERE run_id=? ORDER BY created_at', (run.id,)).fetchall()
    assert [row['status'] for row in rows] == ['failed', 'succeeded']
    assert len(failures) == 1
    assert 'fixture-secret' not in failures[0]
    assert 'https://' not in failures[0]


@pytest.mark.asyncio
async def test_helper_single_attempt_and_legacy_default(monkeypatch):
    from gpt_researcher.utils import llm
    class Provider:
        last_usage_metadata = None
        last_response_metadata = None
        calls = 0
        async def get_chat_response(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError('fixture')
    provider = Provider()
    monkeypatch.setattr(llm, 'get_llm', lambda *a, **k: provider)
    async def no_sleep(_): pass
    monkeypatch.setattr(llm.asyncio, 'sleep', no_sleep)
    with pytest.raises(RuntimeError):
        await llm.create_chat_completion([], model='fixture', max_attempts=1)
    assert provider.calls == 1
    with pytest.raises(RuntimeError):
        await llm.create_chat_completion([], model='fixture')
    assert provider.calls == 11


@pytest.mark.asyncio
async def test_usage_callback_failure_does_not_retry_provider(monkeypatch):
    from gpt_researcher.utils import llm
    class Provider:
        calls = 0
        last_usage_metadata = {'input_tokens': 2}
        last_response_metadata = {'id': 'provider-id'}
        async def get_chat_response(self, *a, **k):
            self.calls += 1
            return ''
    provider = Provider()
    monkeypatch.setattr(llm, 'get_llm', lambda *a, **k: provider)
    def fail(metadata):
        assert metadata['usage'] == {'input_tokens': 2}
        raise ValueError('storage unavailable')
    with pytest.raises(ValueError):
        await llm.create_chat_completion([], model='fixture', usage_callback=fail)
    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['openai', 'azure_openai', 'anthropic', 'ollama'])
async def test_adapter_disables_sdk_retries_and_preserves_unknown_usage(job_context, monkeypatch, provider):
    from gpt_researcher.utils import llm
    from backend.scholarflow.domain.runs import Run
    store, run = job_context
    run = Run(project_id=run.project_id, request_snapshot={'model': {'provider': provider, 'model': 'fixture'}})
    store.records.insert('run', run, project_id=run.project_id)
    observed = {}
    async def helper(messages, **kwargs):
        observed.update(kwargs)
        reason_key = {'ollama': 'done_reason', 'anthropic': 'stop_reason'}.get(provider, 'finish_reason')
        kwargs['usage_callback']({'usage': None, 'response_metadata': {'id': 'test-request', reason_key: 'length'}})
        return 'fixture'
    monkeypatch.setattr(llm, 'create_chat_completion', helper)
    response = await ModelGateway(store.db).complete(ModelRequest(run_id=run.id, purpose='read', messages=[]))
    assert observed['max_attempts'] == 1
    options = observed['llm_kwargs']
    if provider == 'ollama':
        assert 'max_retries' not in options
        assert options['num_predict'] == 4000
    else:
        assert options['max_retries'] == 0
    assert response.usage is None
    assert response.provider_request_id == 'test-request'
    assert response.finish_reason == 'length'


@pytest.mark.asyncio
async def test_empty_response_failure_retains_known_usage(job_context, monkeypatch):
    from gpt_researcher.utils import llm
    store, run = job_context
    async def empty(messages, **kwargs):
        kwargs['usage_callback']({'usage': {'input_tokens': 8, 'output_tokens': 0}, 'response_metadata': {'id': 'empty-result'}})
        raise RuntimeError('Empty response')
    monkeypatch.setattr(llm, 'create_chat_completion', empty)
    with pytest.raises(ScholarError):
        await ModelGateway(store.db).complete(ModelRequest(run_id=run.id, purpose='read', messages=[]))
    with store.db.transaction(write=False) as conn:
        row = conn.execute('SELECT * FROM model_calls').fetchone()
        assert row['status'] == 'failed'
        assert row['provider_request_id'] == 'empty-result'
        assert 'input_tokens' in row['usage_json']
