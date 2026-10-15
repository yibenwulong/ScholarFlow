"""Count each remote attempt before dispatch; credentials stay in the environment."""
import json
from uuid import uuid4

from pydantic import Field

from ..domain.common import Model, utcnow
from ..domain.errors import ScholarError
from ..domain.runs import Run
from ..persistence.run_store import RunStore, fingerprint


class ModelRequest(Model):
    run_id: str
    purpose: str
    messages: list[dict[str, str]]


class ModelResponse(Model):
    text: str
    provider_request_id: str | None = None
    usage: dict | None = None


class ModelConfig(Model):
    provider: str = 'openai'
    model: str = 'gpt-4o-mini'
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=4000, ge=1, le=200000)
    prompt_version: str = 'scholarflow-v1'


class ModelGateway:
    def __init__(self, db, *, dispatch=None, max_calls=100):
        self.db = db
        self.runs = RunStore(db)
        self.dispatch = dispatch
        self.max_calls = max_calls

    async def complete(self, request: ModelRequest) -> ModelResponse:
        call_id = str(uuid4())
        with self.db.transaction() as conn:
            run = self.runs.records.get('run', request.run_id, Run, conn)
            if run.status == 'cancelled':
                raise ScholarError(409, 'run_cancelled', 'This research run has been cancelled')
            count = conn.execute('SELECT COUNT(*) FROM model_calls WHERE run_id=?', (run.id,)).fetchone()[0]
            limit = run.request_snapshot.get('limits', {}).get('max_model_calls', self.max_calls)
            if count >= limit:
                raise ScholarError(409, 'model_budget_exhausted', 'Model call budget exhausted; review inputs before starting a new run')
            conn.execute('INSERT INTO model_calls(id,run_id,request_hash,status,created_at) VALUES (?,?,?,?,?)',
                         (call_id, run.id, fingerprint(request.model_dump()), 'dispatched', utcnow().isoformat()))
            self.runs.records.replace('run', run.model_copy(update={'model_calls': count + 1}),
                                      expected_revision=run.revision, conn=conn)
        try:
            def save_metadata(event):
                meta = event.get('response_metadata') or {}
                usage = event.get('usage')
                with self.db.transaction() as conn:
                    conn.execute('UPDATE model_calls SET provider_request_id=?,usage_json=? WHERE id=?',
                        (meta.get('id') or meta.get('request_id'), json.dumps(usage) if usage is not None else None, call_id))
            result = await self.dispatch(request) if self.dispatch else await self._adapter(request, run, save_metadata)
            result = ModelResponse.model_validate(result)
        except Exception as exc:
            with self.db.transaction() as conn:
                conn.execute("UPDATE model_calls SET status='failed' WHERE id=?", (call_id,))
            if isinstance(exc, ScholarError):
                raise
            root = exc
            while root.__cause__ is not None:
                root = root.__cause__
            status = getattr(root, 'status_code', None)
            retryable = isinstance(root, (TimeoutError, ConnectionError)) or status == 429 or (isinstance(status, int) and status >= 500)
            # HTTPX/OpenAI transport exception types differ; retain only safe type names.
            retryable |= any(word in type(root).__name__.lower() for word in ('timeout', 'connection', 'connecterror'))
            raise ScholarError(502, 'model_request_failed', f'Model request failed ({type(root).__name__})', retryable=retryable) from None
        with self.db.transaction() as conn:
            conn.execute("UPDATE model_calls SET status='succeeded',provider_request_id=?,usage_json=? WHERE id=?",
                         (result.provider_request_id, json.dumps(result.usage) if result.usage is not None else None, call_id))
        return result

    async def _adapter(self, request, run, save_metadata):
        from gpt_researcher.utils.llm import create_chat_completion
        config = ModelConfig.model_validate(run.request_snapshot.get('model', {}))
        # Only providers whose physical retry behavior is known are enabled here.
        if config.provider not in {'openai', 'azure_openai', 'anthropic', 'ollama'}:
            raise ScholarError(422, 'unsupported_model_provider', 'ScholarFlow supports openai, azure_openai, anthropic and ollama')
        options = {'max_retries': 0} if config.provider != 'ollama' else {'num_predict': config.max_tokens}
        metadata = {}
        def capture(event):
            metadata.update(event)
            save_metadata(event)
        text = await create_chat_completion(request.messages, model=config.model, llm_provider=config.provider,
            temperature=config.temperature, max_tokens=config.max_tokens, max_attempts=1,
            llm_kwargs=options, usage_callback=capture)
        response_meta = metadata.get('response_metadata') or {}
        return ModelResponse(text=text, usage=metadata.get('usage'),
                             provider_request_id=response_meta.get('id') or response_meta.get('request_id'))
