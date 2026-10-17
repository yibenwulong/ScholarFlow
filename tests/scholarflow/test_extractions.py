import json

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.evidence import ClaimRevision, Evidence
from backend.scholarflow.domain.library import SourceRecord
from backend.scholarflow.domain.runs import Run
from backend.scholarflow.services.extractions import ExtractionService
from backend.scholarflow.services.model_gateway import ModelResponse
from .test_documents import docs
from .test_library import library


@pytest.fixture
def extraction_context(docs):
    documents, project, paper, library = docs
    source = library.store.records.list('source_record', SourceRecord)[0]
    document = documents.import_abstract(paper.paper_version_id, 'The method uses a graph. Accuracy was 84 percent.', source.id)
    documents.parse(document.id)
    run = Run(project_id=project.id)
    library.store.records.insert('run', run, project_id=project.id)
    return documents, run, paper, document


class FakeModel:
    def __init__(self, output=None):
        self.output = output
        self.requests = []
    async def complete(self, request):
        self.requests.append(request)
        if self.output is not None:
            return ModelResponse(text=self.output)
        inputs = json.loads(request.messages[-1]['content'])
        fragment = inputs['passages'][0]
        text = 'The method uses a graph.' if 'The method uses a graph.' in fragment['text'] else fragment['text']
        return ModelResponse(text=json.dumps(reader_result({'method': [{
            'value': text, 'quote': text, 'passage_id': fragment['passage_id']}]})))


def reader_result(values):
    fields = {name: values.get(name, []) for name in ('research_question', 'method', 'dataset', 'findings', 'limitations')}
    return {'fields': fields, 'missing_reasons': {name: 'Not stated in this fixture fragment.'
                                               for name, items in fields.items() if not items}}


@pytest.mark.asyncio
async def test_extraction_keeps_abstract_boundary_missing_fields_and_human_revisions(extraction_context):
    documents, run, paper, document = extraction_context
    service = ExtractionService(documents.db, FakeModel())
    result = await service.extract(run.id, paper.paper_version_id, [document.id])
    assert result.fields['dataset'].value is None
    claim = service.records.get('claim', result.fields['method'].claim_ids[0], ClaimRevision)
    assert claim.source_scope == 'abstract'
    evidence = service.records.get('evidence', result.fields['method'].evidence_ids[0], Evidence)
    assert evidence.locator_status == 'located'
    changed = service.edit(run.project_id, paper.paper_id, {'method': 'Human correction'}, result.revision)
    assert changed.previous_id == result.id
    assert changed.fields['method'].review_status == 'needs_recheck'
    assert changed.fields['method'].evidence_ids == []
    assert service.get(result.id).fields['method'].value == 'The method uses a graph.'
    with pytest.raises(ScholarError):
        service.edit(run.project_id, paper.paper_id, {'method': 'stale edit'}, result.revision)


@pytest.mark.asyncio
async def test_invalid_json_is_explicit_and_fabricated_evidence_remains_unverified(extraction_context):
    documents, run, paper, document = extraction_context
    service = ExtractionService(documents.db, FakeModel('not json'))
    with pytest.raises(ScholarError) as error:
        await service.extract(run.id, paper.paper_version_id, [document.id])
    assert error.value.code == 'invalid_model_output'
    passage = documents.store.passages(document.id)[0]
    service.gateway = FakeModel(json.dumps(reader_result({'findings': [{
        'value': 'Accuracy was 99 percent.', 'quote': 'Accuracy was 99 percent.', 'passage_id': passage.id}]})))
    result = await service.extract(run.id, paper.paper_version_id, [document.id])
    assert result.fields['findings'].review_status == 'insufficient_evidence'


@pytest.mark.asyncio
async def test_all_source_fragments_are_covered_without_silent_truncation(extraction_context):
    documents, run, paper, document = extraction_context
    model = FakeModel()
    service = ExtractionService(documents.db, model, batch_chars=16)
    result = await service.extract(run.id, paper.paper_version_id, [document.id])
    fragments = result.coverage['fragments']
    assert len(model.requests) > 1
    assert fragments[0]['start'] == 0
    assert fragments[-1]['end'] == len(document.abstract)
    assert sum(f['end'] - f['start'] for f in fragments) == len(document.abstract)
