"""Reader checks every field, audits boundaries, and resumes fenced batches."""
import asyncio
import hashlib
import json

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.evidence import Evidence
from backend.scholarflow.domain.runs import Run
from backend.scholarflow.services.extractions import ExtractionService
from backend.scholarflow.services.model_gateway import ModelResponse
from backend.scholarflow.worker.jobs import ArtifactExecutor
from .test_extractions import extraction_context
from .test_documents import docs
from .test_library import candidate, library


FIELDS = ('research_question', 'method', 'dataset', 'findings', 'limitations')


def payload(inputs, *, empty=False):
    fields = {name: [] for name in FIELDS}
    if not empty:
        fragment = inputs['passages'][0]
        fields['method'] = [{'value': 'A graph method', 'passage_id': fragment['passage_id'], 'quote': fragment['text']}]
    return {'fields': fields, 'missing_reasons': {name: 'Not stated in the supplied fragment.'
                                                for name in FIELDS if not fields[name]}}


class Reader:
    def __init__(self, outputs=()):
        self.outputs, self.requests = list(outputs), []

    async def complete(self, request):
        self.requests.append(request)
        inputs = json.loads(request.messages[-1]['content'])
        result = self.outputs.pop(0) if self.outputs else payload(inputs)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result(inputs)
        return ModelResponse(text=result if isinstance(result, str) else json.dumps(result),
                             provider_request_id='reader-fixture', usage={'input_tokens': 12, 'output_tokens': 8})


def context_service(context, model, **kwargs):
    documents, run, paper, document = context
    return ExtractionService(documents.db, model, **kwargs), run, paper, document


def audit_rows(service):
    with service.db.transaction(write=False) as conn:
        return [json.loads(row['payload']) for row in conn.execute(
            "SELECT payload FROM entities WHERE kind='reader_output_audit' ORDER BY created_at,id")]


@pytest.mark.asyncio
async def test_reader_model_sees_paper_title_language_and_fragments_without_project_topic(extraction_context):
    documents, run, paper, document = extraction_context
    project = documents.library.projects.get(run.project_id)
    documents.library.projects.update(project.id, {'question': 'Sensitive-project-topic-marker'}, project.revision)
    observed = []
    def output(inputs):
        observed.append(inputs)
        return payload(inputs)
    model = Reader([output])
    service = ExtractionService(documents.db, model)
    await service.extract(run.id, paper.paper_version_id, [document.id])
    assert set(observed[0]) == {'paper_title', 'language', 'passages'}
    assert observed[0]['paper_title'] == 'Synthetic paper'
    assert observed[0]['passages'][0]['text'] == document.abstract
    assert 'Sensitive-project-topic-marker' not in json.dumps(model.requests[0].messages)


@pytest.mark.asyncio
async def test_unrelated_project_topic_does_not_turn_paper_metric_batch_into_empty_output(extraction_context):
    documents, run, paper, original = extraction_context
    text = 'Emotion classification achieved F1 0.6500 on setting A and F1 0.3824 on setting B.'
    document = documents.import_abstract(paper.paper_version_id, text, original.source_record_id)
    documents.parse(document.id)
    def output(inputs):
        result = payload(inputs, empty=True)
        if 'question' not in inputs:
            result['fields']['findings'] = [{'value': 'F1 0.6500 on setting A; F1 0.3824 on setting B.',
                'passage_id': inputs['passages'][0]['passage_id'], 'quote': inputs['passages'][0]['text']}]
            del result['missing_reasons']['findings']
        return result
    service = ExtractionService(documents.db, Reader([output]))
    extraction = await service.extract(run.id, paper.paper_version_id, [document.id])
    assert extraction.fields['findings'].value == 'F1 0.6500 on setting A; F1 0.3824 on setting B.'
    assert extraction.fields['findings'].missing_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['empty_object', 'sparse_fields', 'missing_reasons', 'whitespace_reason'])
async def test_reader_does_not_publish_unchecked_or_unexplained_null_fields(extraction_context, invalid):
    def output(inputs):
        if invalid == 'empty_object':
            return {'fields': {}}
        if invalid == 'sparse_fields':
            return {'fields': {'method': payload(inputs)['fields']['method']}}
        result = payload(inputs, empty=True)
        if invalid == 'missing_reasons':
            result.pop('missing_reasons')
        else:
            result['missing_reasons']['dataset'] = '   '
        return result
    service, run, paper, document = context_service(extraction_context, Reader([output]))
    with pytest.raises(ScholarError) as failure:
        await service.extract(run.id, paper.paper_version_id, [document.id])
    assert failure.value.code == 'invalid_model_output'
    assert service.latest(run.project_id, paper.paper_version_id) is None
    audit, = audit_rows(service)
    assert audit['diagnostics'] and failure.value.details['reader_output_audit_id'] == audit['id']


@pytest.mark.asyncio
async def test_all_checked_but_empty_batches_are_not_a_successful_paper(extraction_context):
    model = Reader([lambda inputs: payload(inputs, empty=True)])
    service, run, paper, document = context_service(extraction_context, model)
    with pytest.raises(ScholarError) as failure:
        await service.extract(run.id, paper.paper_version_id, [document.id])
    assert failure.value.code == 'empty_extraction' and failure.value.retryable is False
    assert service.latest(run.project_id, paper.paper_version_id) is None
    assert len(audit_rows(service)) == 1
    assert len(model.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapper', ['raw', 'json_fence', 'plain_fence', 'bom'])
async def test_reader_accepts_complete_json_and_records_exact_immutable_audit(extraction_context, wrapper):
    raws = []
    def output(inputs):
        raw = json.dumps(payload(inputs))
        raw = {'raw': raw, 'json_fence': f'```json\n{raw}\n```',
               'plain_fence': f'```\n{raw}\n```', 'bom': '\ufeff ' + raw}[wrapper]
        raws.append(raw)
        return raw
    service, run, paper, document = context_service(extraction_context, Reader([output]))
    extraction = await service.extract(run.id, paper.paper_version_id, [document.id])
    assert extraction.coverage['reader_version'] == 'reader-v3'
    assert extraction.fields['method'].value == 'A graph method'
    assert extraction.fields['method'].missing_reason is None
    assert extraction.fields['dataset'].value is None
    assert 'abstract' in extraction.fields['dataset'].missing_reason.lower()
    audit, = audit_rows(service)
    assert audit['raw_text'] == raws[0] and audit['sha256'] == hashlib.sha256(raws[0].encode()).hexdigest()
    assert audit['run_id'] == run.id and audit['paper_version_id'] == paper.paper_version_id
    assert audit['provider_request_id'] == 'reader-fixture' and audit['usage']['output_tokens'] == 8
    assert audit['diagnostics'] == []
    from backend.scholarflow.domain.evidence import ReaderOutputAudit
    recorded = service.records.get('reader_output_audit', audit['id'], ReaderOutputAudit)
    with pytest.raises(ValueError, match='immutable'):
        service.records.replace('reader_output_audit', recorded, expected_revision=recorded.revision)


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['prefix', 'suffix', 'multiple', 'truncated', 'invented_field'])
async def test_reader_invalid_response_has_safe_schema_diagnostics_and_raw_audit(extraction_context, invalid):
    def output(inputs):
        result = payload(inputs)
        if invalid == 'invented_field':
            result['Sensitive-field-name-marker'] = 'Sensitive-value-marker'
        raw = json.dumps(result)
        return {'prefix': 'Sensitive-value-marker ' + raw, 'suffix': raw + ' Sensitive-value-marker',
                'multiple': raw + raw, 'truncated': raw[:-5], 'invented_field': raw}[invalid]
    service, run, paper, document = context_service(extraction_context, Reader([output]))
    with pytest.raises(ScholarError) as failure:
        await service.extract(run.id, paper.paper_version_id, [document.id])
    audit, = audit_rows(service)
    assert failure.value.code == 'invalid_model_output'
    assert audit['diagnostics'] and all(set(item) == {'loc', 'type'} for item in audit['diagnostics'])
    assert 'Sensitive-' not in str(failure.value.details) + str(failure.value)
    assert 'Sensitive-' not in str(audit['diagnostics'])


@pytest.mark.asyncio
async def test_reader_unknown_passage_is_rejected_without_rewriting_source_ids(extraction_context):
    def output(inputs):
        result = payload(inputs)
        result['fields']['method'][0]['passage_id'] = 'not-a-supplied-passage'
        return result
    service, run, paper, document = context_service(extraction_context, Reader([output]))
    with pytest.raises(ScholarError) as failure:
        await service.extract(run.id, paper.paper_version_id, [document.id])
    assert failure.value.code == 'unknown_passage'
    assert failure.value.details['reader_output_audit_id'] == audit_rows(service)[0]['id']
    assert service.latest(run.project_id, paper.paper_version_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['ellipsis', 'ascii_ellipsis', 'reordered', 'cross_fragment'])
async def test_reader_rejects_noncontinuous_quotes_before_publishing_a_batch(extraction_context, invalid):
    documents, run, paper, document = extraction_context
    document_ids = [document.id]
    if invalid == 'cross_fragment':
        extra = documents.import_abstract(paper.paper_version_id, 'The second page contains limitations.', document.source_record_id)
        documents.parse(extra.id)
        document_ids.append(extra.id)
    def output(inputs):
        result = payload(inputs)
        quote = {'ellipsis': 'The method… Accuracy was 84 percent.',
                 'ascii_ellipsis': 'The method ... Accuracy was 84 percent.',
                 'reordered': 'Accuracy was 84 percent. The method uses a graph.'}.get(invalid)
        if invalid == 'cross_fragment':
            quote = ' '.join(fragment['text'] for fragment in inputs['passages'])
        result['fields']['method'][0]['quote'] = quote
        return result
    model = Reader([output])
    service = ExtractionService(documents.db, model)
    with pytest.raises(ScholarError) as failure:
        await service.extract(run.id, paper.paper_version_id, document_ids)
    assert failure.value.code == 'invalid_reader_quote' and failure.value.retryable is False
    assert failure.value.details['diagnostics'] == [{'loc': ['fields', 'method', 0, 'quote'], 'type': 'quote_not_contiguous'}]
    assert service.latest(run.project_id, paper.paper_version_id) is None
    batch, = [job for job in service.runs.list_jobs(run.id) if job.stage == 'read_paper_batch']
    assert batch.status == 'failed' and batch.attempt_no == 1 and len(model.requests) == 1
    assert audit_rows(service)[0]['diagnostics'] == failure.value.details['diagnostics']


@pytest.mark.asyncio
async def test_literal_source_ellipsis_and_safe_whitespace_normalization_remain_valid(extraction_context):
    documents, run, paper, original = extraction_context
    document = documents.import_abstract(paper.paper_version_id, 'An inter-\nvention uses ... a graph.', original.source_record_id)
    documents.parse(document.id)
    def output(inputs):
        result = payload(inputs)
        result['fields']['method'][0]['quote'] = 'An intervention uses ... a graph.'
        return result
    service = ExtractionService(documents.db, Reader([output]))
    extraction = await service.extract(run.id, paper.paper_version_id, [document.id])
    evidence = service.records.get('evidence', extraction.fields['method'].evidence_ids[0], Evidence)
    assert evidence.locator_status == 'located' and evidence.quote_start == 0


@pytest.mark.asyncio
async def test_successful_batches_survive_later_failure_and_cross_run_retry_only_reads_failed_batches(extraction_context):
    model = Reader([lambda inputs: payload(inputs), 'not JSON'])
    service, run, paper, document = context_service(extraction_context, model, batch_chars=16)
    with pytest.raises(ScholarError):
        await service.extract(run.id, paper.paper_version_id, [document.id])
    assert service.latest(run.project_id, paper.paper_version_id) is None
    batches = [job for job in service.runs.list_jobs(run.id) if job.stage == 'read_paper_batch']
    assert sorted(job.status for job in batches) == ['failed', 'succeeded']
    retry_run = Run(project_id=run.project_id, request_snapshot=run.request_snapshot)
    service.records.insert('run', retry_run, project_id=run.project_id)
    extraction = await service.extract(retry_run.id, paper.paper_version_id, [document.id])
    starts = [json.loads(request.messages[-1]['content'])['passages'][0]['start'] for request in model.requests]
    assert starts == [0, 16, 16, 32, 48]
    assert extraction.coverage['complete'] is True and extraction.coverage['reader_version'] == 'reader-v3'
    assert len(audit_rows(service)) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['project', 'documents', 'fragment_hash', 'output_schema'])
async def test_batch_cache_requires_matching_provenance_and_valid_output(extraction_context, invalid):
    from backend.scholarflow.domain.evidence import ReaderOutputAudit
    from uuid import uuid4
    model = Reader()
    service, run, paper, document = context_service(extraction_context, model)
    extraction = await service.extract(run.id, paper.paper_version_id, [document.id])
    batch, = [job for job in service.runs.list_jobs(run.id) if job.stage == 'read_paper_batch']
    audit = service.records.get('reader_output_audit', batch.result['reader_output_audit_id'], ReaderOutputAudit)
    if invalid == 'fragment_hash':
        with service.db.transaction() as conn:
            row = conn.execute('SELECT payload FROM jobs WHERE id=?', (batch.id,)).fetchone()
            value = json.loads(row['payload'])
            value['manifest']['fragments'][0]['sha256'] = 'different-fragment-hash'
            conn.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(value), batch.id))
    else:
        changes = {'id': str(uuid4())}
        if invalid == 'project':
            changes['project_id'] = 'another-project'
        elif invalid == 'documents':
            changes['document_version_ids'] = ['another-document']
        else:
            raw = '{"fields":{}}'
            changes.update(raw_text=raw, sha256=hashlib.sha256(raw.encode()).hexdigest())
        wrong = audit.model_copy(update=changes)
        service.records.insert('reader_output_audit', wrong, project_id=run.project_id)
        with service.db.transaction() as conn:
            conn.execute('UPDATE jobs SET result=? WHERE id=?',
                         (json.dumps({'reader_output_audit_id': wrong.id}), batch.id))
    retry_run = Run(project_id=run.project_id, request_snapshot=run.request_snapshot)
    service.records.insert('run', retry_run, project_id=run.project_id)
    with pytest.raises(ScholarError) as failure:
        await service.extract(retry_run.id, paper.paper_version_id, [document.id])
    assert failure.value.code == 'invalid_reader_batch_cache'
    assert service.latest(run.project_id, paper.paper_version_id).id == extraction.id
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_raw_audit_without_a_successful_batch_job_is_not_a_cache_hit(extraction_context):
    model = Reader()
    service, run, paper, document = context_service(extraction_context, model)
    await service.extract(run.id, paper.paper_version_id, [document.id])
    batch, = [job for job in service.runs.list_jobs(run.id) if job.stage == 'read_paper_batch']
    with service.db.transaction() as conn:
        conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (batch.id,))
    retry_run = Run(project_id=run.project_id, request_snapshot=run.request_snapshot)
    service.records.insert('run', retry_run, project_id=run.project_id)
    await service.extract(retry_run.id, paper.paper_version_id, [document.id])
    assert len(model.requests) == 2 and len(audit_rows(service)) == 2


@pytest.mark.asyncio
async def test_batch_executor_preserves_two_concurrent_paper_reads(extraction_context):
    from backend.scholarflow.domain.library import SourceRecord
    documents, run, first_paper, first_document = extraction_context
    second_paper = documents.library.import_candidates(run.project_id, [candidate('parallel-paper')])[0]
    source = next(item for item in documents.store.records.list('source_record', SourceRecord)
                  if item.paper_version_id == second_paper.paper_version_id)
    second_document = documents.import_abstract(second_paper.paper_version_id, 'A second graph method.', source.id)
    documents.parse(second_document.id)
    class ConcurrentReader(Reader):
        def __init__(self):
            super().__init__()
            self.active, self.peak = 0, 0
            self.entered_two, self.release = asyncio.Event(), asyncio.Event()
        async def complete(self, request):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 2:
                self.entered_two.set()
            try:
                await self.release.wait()
                return await super().complete(request)
            finally:
                self.active -= 1
    model = ConcurrentReader()
    service = ExtractionService(documents.db, model)
    tasks = [asyncio.create_task(service.extract(run.id, paper.paper_version_id, [document.id]))
             for paper, document in [(first_paper, first_document), (second_paper, second_document)]]
    try:
        await asyncio.wait_for(model.entered_two.wait(), timeout=1)
    except TimeoutError:
        pass
    finally:
        model.release.set()
        await asyncio.gather(*tasks)
    assert model.peak == 2 and len(model.requests) == 2


@pytest.mark.asyncio
async def test_batch_network_retry_exhaustion_is_not_retried_again_by_paper_executor(extraction_context):
    error = ScholarError(502, 'model_request_failed', 'Fixture connection failure', retryable=True)
    model = Reader([error, error, error, error])
    service, run, paper, document = context_service(extraction_context, model)
    async def no_wait(_):
        pass
    service.batch_executor.sleep = no_wait
    outer = ArtifactExecutor(service.runs, sleep=no_wait)
    with pytest.raises(ScholarError) as failure:
        await outer.execute(run.id, 'read_paper', {'paper_version_id': paper.paper_version_id},
                            lambda: service.extract(run.id, paper.paper_version_id, [document.id]))
    assert failure.value.code == 'model_request_failed' and failure.value.retryable is False
    assert len(model.requests) == 3
    jobs = service.runs.list_jobs(run.id)
    assert next(job for job in jobs if job.stage == 'read_paper').attempt_no == 1
    assert next(job for job in jobs if job.stage == 'read_paper_batch').attempt_no == 3
    assert audit_rows(service) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['lease_lost', 'run_cancelled', 'model_budget_exhausted'])
async def test_batch_control_errors_keep_their_codes(extraction_context, code):
    service, run, paper, document = context_service(extraction_context,
        Reader([ScholarError(409, code, 'Fixture control signal')]))
    with pytest.raises(ScholarError) as failure:
        await service.extract(run.id, paper.paper_version_id, [document.id])
    assert failure.value.code == code
    assert service.latest(run.project_id, paper.paper_version_id) is None


@pytest.mark.asyncio
async def test_repeated_quote_in_later_chunk_is_located_within_that_fragment(extraction_context):
    documents, run, paper, original = extraction_context
    document = documents.import_abstract(paper.paper_version_id, 'repeat phrase ..... repeat phrase', original.source_record_id)
    documents.parse(document.id)
    def output(inputs):
        result = payload(inputs, empty=True)
        if inputs['passages'][0]['start'] == 20:
            result['fields']['method'] = [{'value': 'Repeated method', 'quote': 'repeat phrase',
                                         'passage_id': inputs['passages'][0]['passage_id']}]
            del result['missing_reasons']['method']
        return result
    service = ExtractionService(documents.db, Reader([output, output]), batch_chars=20)
    extraction = await service.extract(run.id, paper.paper_version_id, [document.id])
    evidence = service.records.get('evidence', extraction.fields['method'].evidence_ids[0], Evidence)
    assert evidence.locator_status == 'located' and evidence.quote_start == 20 and evidence.quote_end == 33
