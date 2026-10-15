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
        kwargs['usage_callback']({'usage': None, 'response_metadata': {'id': 'test-request'}})
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
