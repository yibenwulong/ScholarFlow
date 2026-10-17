import json

import pytest

from backend.scholarflow.api.dependencies import ScholarServices
from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.library import PaperCandidate
from backend.scholarflow.domain.projects import ProjectCreate, PlanDraft, PlanRevision
from backend.scholarflow.domain.runs import RunAction, RunCreate
from backend.scholarflow.services.model_gateway import ModelResponse
from backend.scholarflow.services.providers.base import SearchResult
from backend.scholarflow.workflows.graph import WorkflowRuntime
from backend.scholarflow.worker.runner import Worker
from .test_extractions import reader_result


class ResearchModel:
    def __init__(self, *, conflict=False):
        self.conflict = conflict
        self.purposes = []
    async def complete(self, request):
        self.purposes.append(request.purpose)
        inputs = json.loads(request.messages[-1]['content'])
        if request.purpose == 'plan':
            output = {'queries': ['graph methods'], 'subquestions': ['Which methods?']}
        elif request.purpose == 'screen':
            output = {'recommendations': [{'paper_version_id': p['id'], 'decision': 'included', 'reason': 'Relevant abstract'} for p in inputs['papers']]}
        elif request.purpose == 'read_paper':
            passage = inputs['passages'][0]
            output = reader_result({'method': [{'value': 'The method uses a graph.', 'quote': 'The method uses a graph.', 'passage_id': passage['passage_id']}]})
        elif request.purpose == 'write_report':
            entry = inputs['extractions'][0]
            field = entry['fields']['method']
            text = 'The method uses a graph.'
            output = {'title': 'Research report', 'blocks': [{'id': 'method', 'heading': 'Methods',
                'text': text + f" [@{entry['key']}]", 'claims': [{'start': 0, 'end': len(text), 'token_index': 0,
                    'claim_revision_ids': field['claim_ids'], 'evidence_ids': field['evidence_ids']}]}]}
        elif request.purpose == 'verify_claim':
            output = {'status': 'conflicting' if self.conflict else 'supported', 'reason': 'Fixture support assessment'}
        else:
            raise AssertionError(request.purpose)
        return ModelResponse(text=json.dumps(output))


class Provider:
    name = 'arxiv'
    def __init__(self): self.calls = []
    async def search(self, query, limit=20):
        self.calls.append(query)
        return SearchResult(source='arxiv', query=query, candidates=[PaperCandidate(
            title=f'Paper {i}', source='arxiv', source_id=f'2601.0000{i}v1',
            identifiers={'arxiv': f'2601.0000{i}v1'}, abstract='The method uses a graph.') for i in range(2)][:limit])


@pytest.fixture
def workflow_context(settings):
    services = ScholarServices.create(settings)
    model, provider = ResearchModel(), Provider()
    services.configure_models(model)
    project = services.projects.create(ProjectCreate(question='Compare graph methods', sources=['arxiv']))
    return services, model, provider, project


async def tick(services, provider):
    async with WorkflowRuntime(services, providers={'arxiv': provider}) as runtime:
        assert await Worker(services.runs.store, driver=runtime.driver).run_once()


@pytest.mark.asyncio
async def test_plan_and_selection_gates_then_completed_report(workflow_context):
    services, model, provider, project = workflow_context
    run = services.runs.create(project.id, RunCreate(), 'start')
    await tick(services, provider)
    run = services.runs.get(run.id)
    assert run.status == 'waiting_for_user'
    assert run.pending_action['type'] == 'approve_plan'
    assert provider.calls == []
    plan_id = run.pending_action['plan_revision_id']
    plan = services.projects.store.records.get('plan', plan_id, PlanRevision)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    services.runs.act(run.id, RunAction(action='continue', expected_revision=run.revision, plan_revision_id=plan.id), 'approve')
    await tick(services, provider)
    run = services.runs.get(run.id)
    assert run.status == 'waiting_for_user'
    assert run.pending_action['type'] == 'confirm_selection'
    changed_plan = services.projects.save_plan(project.id, PlanDraft(queries=['a different search']), plan.revision)
    services.projects.approve_plan(project.id, changed_plan.id, changed_plan.revision)
    papers = services.library.list_project(project.id)
    selection = services.library.freeze_selection(project.id, [papers[0]['paper_version_id']], 0)
    with pytest.raises(ScholarError) as frozen:
        services.runs.act(run.id, RunAction(action='continue', expected_revision=run.revision,
            plan_revision_id=changed_plan.id, selection_id=selection.id), 'changed-plan')
    assert frozen.value.code == 'manifest_frozen'
    services.runs.act(run.id, RunAction(action='continue', expected_revision=run.revision, selection_id=selection.id), 'select')
    await tick(services, provider)
    run = services.runs.get(run.id)
    assert run.status == 'completed'
    assert run.manifests['selection']['paper_version_ids'] == selection.paper_version_ids
    assert len(run.manifests['extractions']['extraction_ids']) == 1
    assert model.purposes.count('write_report') == 1


def test_entry_requirements_and_idempotency(workflow_context):
    services, _, _, project = workflow_context
    with pytest.raises(ScholarError):
        services.runs.create(project.id, RunCreate(start_at='search'), 'missing')
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graphs']), 0)
    with pytest.raises(ScholarError):
        services.runs.create(project.id, RunCreate(start_at='search', plan_revision_id=plan.id), 'unapproved')
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    original = services.runs.create(project.id, RunCreate(start_at='search', plan_revision_id=plan.id), 'search')
    assert services.runs.create(project.id, RunCreate(start_at='search', plan_revision_id=plan.id), 'search').id == original.id
    with pytest.raises(ScholarError):
        services.runs.create(project.id, RunCreate(), 'search')
    with pytest.raises(ScholarError):
        services.runs.create(project.id, RunCreate(start_at='analyze', plan_revision_id=plan.id), 'analyze')


@pytest.mark.asyncio
async def test_analyze_entry_skips_search_and_rewrite_cap_never_accepts_draft(workflow_context):
    services, model, provider, project = workflow_context
    model.conflict = True
    candidates = (await provider.search('fixture')).candidates
    papers = services.library.import_candidates(project.id, candidates)
    selection = services.library.freeze_selection(project.id, [papers[0].paper_version_id], 0)
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    run = services.runs.create(project.id, RunCreate(start_at='analyze', plan_revision_id=plan.id, selection_id=selection.id), 'analyze')
    await tick(services, provider)
    run = services.runs.get(run.id)
    assert len(provider.calls) == 1
    assert run.status == 'needs_review'
    assert run.quality_status == 'issues_present'
    assert model.purposes.count('write_report') == 2
    assert 'acceptance_id' not in run.manifests
    with pytest.raises(ScholarError):
        services.runs.act(run.id, RunAction(action='continue', expected_revision=run.revision), 'unsafe')
    result = services.runs.act(run.id, RunAction(action='accept_draft', expected_revision=run.revision,
        report_version_id=run.pending_action['report_version_id'], verification_record_id=run.pending_action['verification_record_id']), 'accept')
    assert result.status == 'completed'
    assert result.quality_status == 'issues_present'
    with pytest.raises(ScholarError):
        services.runs.act(run.id, RunAction(action='continue', expected_revision=result.revision), 'again')


@pytest.mark.asyncio
async def test_unreadable_included_paper_is_visible_in_report_quality(workflow_context):
    services, _, provider, project = workflow_context
    candidates = (await provider.search('fixture')).candidates
    candidates[1] = candidates[1].model_copy(update={'abstract': None})
    papers = services.library.import_candidates(project.id, candidates)
    selection = services.library.freeze_selection(project.id, [p.paper_version_id for p in papers], 0)
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    run = services.runs.create(project.id, RunCreate(start_at='analyze', plan_revision_id=plan.id, selection_id=selection.id), 'start')
    await tick(services, provider)
    result = services.runs.get(run.id)
    report = services.reports.get(result.manifests['final_report']['report_version_id'])
    assert report.manifest['coverage']['unreadable_paper_version_ids'] == [papers[1].paper_version_id]
    assert result.status == 'needs_review'
    assert result.quality_status == 'issues_present'
