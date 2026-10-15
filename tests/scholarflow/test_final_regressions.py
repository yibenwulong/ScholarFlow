import json

import pytest

from backend.scholarflow.domain.operations import EvidenceReview
from backend.scholarflow.domain.projects import PlanDraft
from backend.scholarflow.domain.runs import RunCreate
from .test_workflow_gates import workflow_context, tick


async def analyzed(context):
    services, model, provider, project = context
    papers = services.library.import_candidates(project.id, (await provider.search('fixture')).candidates)
    selection = services.library.freeze_selection(project.id, [papers[0].paper_version_id], 0)
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    data = RunCreate(start_at='analyze', plan_revision_id=plan.id, selection_id=selection.id)
    run = services.runs.create(project.id, data, 'initial')
    await tick(services, provider)
    return services.runs.get(run.id), data, papers[0]


@pytest.mark.asyncio
async def test_explicit_recheck_observes_new_human_review(workflow_context):
    services, _, provider, project = workflow_context
    run, _, _ = await analyzed(workflow_context)
    report_id = run.manifests['final_report']['report_version_id']
    first = services.runs.create(project.id, RunCreate(kind='report_check', target_id=report_id), 'check1')
    await tick(services, provider)
    old_id = services.runs.get(first.id).manifests['result']['verification_record_id']
    occurrence = services.reports.get(report_id).occurrences[0]
    evidence_id = occurrence.evidence_ids[0]
    review = EvidenceReview(evidence_id=evidence_id, verdict='conflicting', reason='Human found a source contradiction')
    services.runs.store.records.insert('evidence_review', review, project_id=project.id, links=[('evidence', evidence_id)])
    second = services.runs.create(project.id, RunCreate(kind='report_check', target_id=report_id), 'check2')
    await tick(services, provider)
    result = services.runs.get(second.id)
    assert result.manifests['result']['verification_record_id'] != old_id
    assert result.quality_status == 'issues_present'


@pytest.mark.asyncio
async def test_new_analysis_freezes_human_matrix_revision(workflow_context):
    services, model, provider, project = workflow_context
    original, data, paper = await analyzed(workflow_context)
    old_ids = original.manifests['extractions']['extraction_ids']
    extraction = services.extractions.get(old_ids[0])
    correction = services.extractions.edit(project.id, paper.paper_id, {'method': 'Human correction'}, extraction.revision, published_only=True)
    run = services.runs.create(project.id, data, 'regenerate')
    services.extractions.edit(project.id, paper.paper_id, {'method': 'A later correction'}, correction.revision, published_only=True)
    reads = model.purposes.count('read_paper')
    await tick(services, provider)
    current = services.runs.get(run.id)
    assert current.manifests['extractions']['extraction_ids'] == [correction.id]
    assert model.purposes.count('read_paper') == reads
    assert services.runs.get(original.id).manifests['extractions']['extraction_ids'] == old_ids


@pytest.mark.asyncio
async def test_screening_receives_exact_approved_plan(workflow_context):
    services, model, provider, project = workflow_context
    captured = []
    complete = model.complete
    async def record(request):
        if request.purpose == 'screen':
            captured.append(json.loads(request.messages[-1]['content']))
        return await complete(request)
    model.complete = record
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph'], inclusion_criteria=['Only randomized studies'],
        exclusion_criteria=['No reviews'], subquestions=['Which graph method?']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    run = services.runs.create(project.id, RunCreate(start_at='search', plan_revision_id=plan.id), 'screen')
    await tick(services, provider)
    assert captured[0]['approved_plan']['id'] == plan.id
    assert captured[0]['approved_plan']['inclusion_criteria'] == ['Only randomized studies']
    assert captured[0]['approved_plan']['exclusion_criteria'] == ['No reviews']
    assert captured[0]['approved_plan']['subquestions'] == ['Which graph method?']


@pytest.mark.asyncio
async def test_document_preparation_uses_later_source_abstract(workflow_context):
    from backend.scholarflow.domain.library import PaperCandidate, SourceRecord
    services, _, provider, project = workflow_context
    paper = services.library.import_candidates(project.id, [PaperCandidate(title='Graph', source='semantic_scholar',
        source_id='one', identifiers={'doi': '10.1000/missing'})])[0]
    services.library.import_candidates(project.id, [PaperCandidate(title='Graph', source='openalex', source_id='W456',
        identifiers={'doi': '10.1000/missing'}, abstract='The method uses a graph.')])
    plan = services.projects.save_plan(project.id, PlanDraft(queries=['graph']), 0)
    services.projects.approve_plan(project.id, plan.id, plan.revision)
    selection = services.library.freeze_selection(project.id, [paper.paper_version_id], 0)
    run = services.runs.create(project.id, RunCreate(start_at='analyze', plan_revision_id=plan.id, selection_id=selection.id), 'enriched')
    await tick(services, provider)
    result = services.runs.get(run.id)
    entry = result.manifests['documents']['papers'][0]
    assert entry['document_version_ids']
    document = services.documents.get(entry['document_version_ids'][0])
    source = services.runs.store.records.get('source_record', document.source_record_id, SourceRecord)
    assert source.source == 'openalex'
