from contextlib import AsyncExitStack

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..domain.errors import ScholarError
from .nodes import ResearchNodes
from .state import ResearchState


def thread_config(run):
    return {'configurable': {'thread_id': f'{run.id}:{run.execution_generation}'}}


def build_graph(services, checkpointer, *, providers=None):
    nodes = ResearchNodes(services, providers=providers)
    builder = StateGraph(ResearchState)
    names = ['plan', 'approve_plan', 'search', 'screen', 'confirm_selection', 'documents', 'read', 'write', 'verify', 'finish']
    for name in names:
        builder.add_node(name, getattr(nodes, name))
    builder.add_edge(START, 'plan')
    for left, right in zip(names, names[1:]):
        if left in {'screen', 'read'}:
            builder.add_conditional_edges(left, lambda state: 'stop' if state['stage'] == 'stop' else 'next', {'stop': END, 'next': right})
        else:
            builder.add_edge(left, right)
    builder.add_edge('finish', END)
    return builder.compile(checkpointer=checkpointer)


class WorkflowRuntime:
    def __init__(self, services, *, providers=None):
        self.services = services
        self.providers = providers
        self.stack = AsyncExitStack()
        self.graph = None

    async def __aenter__(self):
        checkpointer = await self.stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(self.services.settings.checkpoint_path)))
        checkpointer.serde = JsonPlusSerializer(allowed_msgpack_modules=None)
        await checkpointer.setup()
        self.graph = build_graph(self.services, checkpointer, providers=self.providers)
        return self

    async def __aexit__(self, *args):
        await self.stack.__aexit__(*args)

    async def drive_run(self, run_id, resume_payload=None, *, generation=None):
        store = self.services.runs.store
        run = store.get_run(run_id)
        captured_generation = run.execution_generation if generation is None else generation
        if captured_generation != run.execution_generation:
            raise ScholarError(409, 'lease_lost', 'This execution has been replaced')
        if run.kind != 'literature_review':
            from ..worker.auxiliary import drive_auxiliary
            return await drive_auxiliary(self.services, run, captured_generation)
        config = thread_config(run)
        state = await self.graph.aget_state(config)
        if state.values:
            interrupted = any(task.interrupts for task in state.tasks)
            payload = Command(resume=resume_payload or {}) if interrupted else None
        else:
            payload = {'run_id': run.id, 'generation': captured_generation, 'stage': run.stage}
        try:
            await self.graph.ainvoke(payload, config)
        except Exception as exc:
            def budget_error(error):
                if isinstance(error, ScholarError) and error.code == 'model_budget_exhausted':
                    return error
                if isinstance(error, BaseExceptionGroup):
                    return next((found for child in error.exceptions if (found := budget_error(child))), None)
                return None
            budget = budget_error(exc)
            if budget is None:
                raise
            store.update_run(run.id, {'status': 'needs_review', 'pending_action': {'type': 'budget_exhausted'},
                'issues': [{'code': budget.code, 'message': budget.message}]}, expected_generation=captured_generation)
        return store.get_run(run_id)

    async def driver(self, lease):
        run = await self.drive_run(lease.run_id, lease.input_manifest.get('resume'), generation=lease.generation)
        return {'run_id': run.id, 'status': run.status, 'generation': lease.generation}
