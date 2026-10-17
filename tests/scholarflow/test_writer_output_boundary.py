"""Writer formatting tolerance must not weaken saved report/evidence contracts."""
import hashlib
import json

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.evidence import ClaimRevision, Evidence
from backend.scholarflow.domain.reports import ReportBlock
from backend.scholarflow.domain.reports import WriterOutputAudit
from backend.scholarflow.persistence.run_store import fingerprint
from backend.scholarflow.services.extractions import ExtractionService
from backend.scholarflow.services.model_gateway import ModelResponse
from backend.scholarflow.services.reports import ReportService
from .test_extractions import FakeModel, extraction_context
from .test_documents import docs
from .test_library import library
from .test_report_versions import SupportVerifier


@pytest.fixture
async def writer_boundary_context(extraction_context):
    documents, run, paper, document = extraction_context
    extraction = await ExtractionService(documents.db, FakeModel()).extract(
        run.id, paper.paper_version_id, [document.id])
    field = extraction.fields['method']
    key = 'sf_' + paper.paper_version_id.replace('-', '')
    statement = 'The method uses a graph.'
    output = {'title': 'Evidence review', 'blocks': [{
        'id': 'method', 'text': statement + f' [@{key}]', 'claims': [{
            'start': 0, 'end': len(statement), 'token_index': 0,
            'claim_revision_ids': field.claim_ids, 'evidence_ids': field.evidence_ids}]}]}
    return documents, run, extraction, output


class RecordedWriter:
    def __init__(self, text, finish_reason=None):
        self.text = text
        self.finish_reason = finish_reason
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return ModelResponse(text=self.text, provider_request_id='writer-request-fixture',
                             usage={'input_tokens': 20, 'output_tokens': 30}, finish_reason=self.finish_reason)


def writer_service(context, text):
    documents, run, extraction, _ = context
    model = RecordedWriter(text)
    return ReportService(documents.db, model, verifier=SupportVerifier()), model, run, extraction


def audit_rows(service):
    with service.db.transaction(write=False) as conn:
        return [json.loads(row['payload']) for row in conn.execute(
            "SELECT payload FROM entities WHERE kind='writer_output_audit' ORDER BY created_at,id")]


@pytest.mark.asyncio
@pytest.mark.parametrize('complete_json', [False, True])
@pytest.mark.parametrize('finish_reason', ['length', 'max_tokens'])
async def test_truncated_writer_response_is_audited_and_never_saved(writer_boundary_context, complete_json, finish_reason):
    payload = json.dumps(writer_boundary_context[3])
    text = payload if complete_json else payload[:-4]
    service, model, run, extraction = writer_service(writer_boundary_context, text)
    model.finish_reason = finish_reason
    with pytest.raises(ScholarError) as failure:
        await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert failure.value.code == 'writer_output_truncated'
    assert failure.value.retryable is False
    audit, = audit_rows(service)
    assert audit['raw_text'] == text
    assert audit['finish_reason'] == finish_reason
    assert audit['id'] == failure.value.details['writer_output_audit_id']
    assert service.store.list(run.project_id) == []
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_writer_receives_actual_claim_evidence_pairs_not_array_position_guesses(writer_boundary_context):
    from uuid import uuid4
    service, model, run, extraction = writer_service(writer_boundary_context, json.dumps(writer_boundary_context[3]))
    field = extraction.fields['method']
    claim = service.records.get('claim', field.claim_ids[0], ClaimRevision)
    evidence = service.records.get('evidence', field.evidence_ids[0], Evidence)
    other_claim = claim.model_copy(update={'id': str(uuid4()), 'text': 'Another finding.'})
    other_evidence = evidence.model_copy(update={'id': str(uuid4()), 'claim_revision_id': other_claim.id})
    service.records.insert('claim', other_claim, project_id=run.project_id)
    service.records.insert('evidence', other_evidence, project_id=run.project_id)
    fields = dict(extraction.fields, method=field.model_copy(update={
        'claim_ids': [claim.id, other_claim.id], 'evidence_ids': [other_evidence.id, evidence.id]}))
    amended = extraction.model_copy(update={'id': str(uuid4()), 'fields': fields})
    service.records.insert('extraction', amended, project_id=run.project_id)
    await service.compose(run.id, {'extraction_ids': [amended.id]})
    inputs = json.loads(model.requests[0].messages[-1]['content'])
    pairs = inputs['extractions'][0]['fields']['method']['citation_pairs']
    assert {(pair['claim_revision_id'], pair['evidence_id']) for pair in pairs} == {
        (claim.id, evidence.id), (other_claim.id, other_evidence.id)}
    assert 'claim_ids' not in inputs['extractions'][0]['fields']['method']
    assert 'evidence_ids' not in inputs['extractions'][0]['fields']['method']


@pytest.mark.asyncio
async def test_saved_coverage_tracks_citations_removed_and_restored_by_user(writer_boundary_context):
    service, _, run, extraction = writer_service(writer_boundary_context, json.dumps(writer_boundary_context[3]))
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    first = service.save(run.project_id, draft, 0)
    assert first.manifest['coverage']['cited_paper_version_ids'] == [extraction.paper_version_id]
    edited = draft.model_copy(update={'blocks': [ReportBlock(id='method', text='No cited statements.')]})
    second = service.save(run.project_id, edited, first.revision)
    assert second.manifest['coverage']['cited_paper_version_ids'] == []
    assert second.manifest['coverage']['uncited_paper_version_ids'] == [extraction.paper_version_id]
    third = service.save(run.project_id, draft, second.revision)
    assert third.manifest['coverage']['cited_paper_version_ids'] == [extraction.paper_version_id]
    assert third.manifest['coverage']['uncited_paper_version_ids'] == []


@pytest.mark.asyncio
async def test_writer_preserves_explicit_section_lists_with_audited_normalization(writer_boundary_context):
    output = writer_boundary_context[3]
    for name in ['limitations', 'next_directions', 'appendix']:
        output[name] = [dict(output['blocks'][0], id=name)]
    text = json.dumps(output)
    service, _, run, extraction = writer_service(writer_boundary_context, text)
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert [block.id for block in draft.blocks] == ['method', 'limitations', 'next_directions', 'appendix']
    assert [block.text for block in draft.blocks] == [output['blocks'][0]['text']] * 4
    audit, = audit_rows(service)
    assert audit['raw_text'] == text
    assert {tuple(item['loc']) for item in audit['normalizations']} == {('limitations',), ('next_directions',), ('appendix',)}
    assert service.save(run.project_id, draft, 0).blocks == draft.blocks


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch', [None, 'request', 'provider', 'hash', 'usage', 'audit_input'])
async def test_failed_schema_output_reparse_requires_matching_request_and_immutable_response(writer_boundary_context, mismatch):
    service, model, run, extraction = writer_service(writer_boundary_context, json.dumps(writer_boundary_context[3]))
    await service.compose(run.id, {'extraction_ids': [extraction.id]})
    request = model.requests[0]
    output = writer_boundary_context[3]
    output['appendix'] = [dict(output['blocks'][0], id='appendix')]
    text = json.dumps(output)
    old = WriterOutputAudit(project_id=run.project_id, run_id=run.id, raw_text=text,
        sha256='wrong-hash' if mismatch == 'hash' else hashlib.sha256(text.encode()).hexdigest(),
        provider_request_id='writer-request-fixture', output_format='json', finish_reason='stop',
        usage={'output_tokens': 999} if mismatch == 'usage' else None,
        input_hash='wrong-input' if mismatch == 'audit_input' else None,
        diagnostics=[{'loc': ['<extra_field>'], 'type': 'extra_forbidden'}])
    service.records.insert('writer_output_audit', old, project_id=run.project_id)
    with service.db.transaction() as conn:
        conn.execute('INSERT INTO model_calls(id,run_id,request_hash,status,provider_request_id,created_at) VALUES (?,?,?,?,?,?)',
            ('fixture-call', run.id, 'wrong-request' if mismatch == 'request' else fingerprint(request.model_dump()),
             'succeeded', 'wrong-provider' if mismatch == 'provider' else old.provider_request_id, old.created_at.isoformat()))
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    latest = next(audit for audit in audit_rows(service) if audit['id'] == draft.manifest['writer_output_audit_id'])
    if mismatch is None:
        assert len(model.requests) == 1
        assert latest['reused_from_audit_id'] == old.id
        assert latest['raw_text'] == old.raw_text
        assert [block.id for block in draft.blocks] == ['method', 'appendix']
    else:
        assert len(model.requests) == 2
        assert latest.get('reused_from_audit_id') is None
    with service.db.transaction(write=False) as conn:
        assert conn.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 1


@pytest.mark.asyncio
async def test_real_input_ids_from_another_paper_cannot_bind_this_citation(writer_boundary_context):
    from backend.scholarflow.domain.library import PaperCandidate, SourceRecord
    documents, run, extraction, output = writer_boundary_context
    other = documents.library.import_candidates(run.project_id, [PaperCandidate(
        title='Another paper', source='local', source_id='other', abstract='The method uses a graph.')])[0]
    source = next(row for row in documents.store.records.list('source_record', SourceRecord) if row.source_id == 'other')
    document = documents.import_abstract(other.paper_version_id, 'The method uses a graph.', source.id)
    documents.parse(document.id)
    other_extraction = await ExtractionService(documents.db, FakeModel()).extract(run.id, other.paper_version_id, [document.id])
    field = other_extraction.fields['method']
    output['blocks'][0]['claims'][0].update(claim_revision_ids=field.claim_ids, evidence_ids=field.evidence_ids)
    service, _, _, _ = writer_service(writer_boundary_context, json.dumps(output))
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id, other_extraction.id]})
    assert draft.blocks[0].claims == []
    assert draft.manifest['writer_issues'][0]['code'] == 'invalid_claim_source'
    saved = service.save(run.project_id, draft, 0)
    unsafe = draft.model_copy(update={'blocks': [ReportBlock(**output['blocks'][0])]})
    with pytest.raises(ScholarError) as failure:
        service.save(run.project_id, unsafe, saved.revision)
    assert failure.value.code == 'citation_source_mismatch'


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapper', ['raw', 'json_fence', 'plain_fence', 'bom'])
async def test_writer_accepts_only_complete_json_wrappers_and_audits_exact_response(writer_boundary_context, wrapper):
    payload = json.dumps(writer_boundary_context[3])
    text = {'raw': payload, 'json_fence': f'```json\n{payload}\n```',
            'plain_fence': f'```\n{payload}\n```', 'bom': '\ufeff  ' + payload + '\n'}[wrapper]
    service, model, run, extraction = writer_service(writer_boundary_context, text)
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert draft.blocks[0].text == writer_boundary_context[3]['blocks'][0]['text']
    assert draft.blocks[0].claims == writer_boundary_context[3]['blocks'][0]['claims']
    assert 'writer_output_audit_id' in draft.manifest
    audit, = audit_rows(service)
    assert audit['id'] == draft.manifest['writer_output_audit_id']
    assert audit['run_id'] == run.id
    assert audit['raw_text'] == text
    assert audit['sha256'] == hashlib.sha256(text.encode('utf-8')).hexdigest()
    assert audit['provider_request_id'] == 'writer-request-fixture'
    assert audit['usage'] == {'input_tokens': 20, 'output_tokens': 30}
    assert audit['diagnostics'] == []
    assert len(model.requests) == 1
    assert service.store.list(run.project_id) == []
    from backend.scholarflow.domain.reports import WriterOutputAudit
    recorded = service.records.get('writer_output_audit', audit['id'], WriterOutputAudit)
    with pytest.raises(ValueError, match='immutable'):
        service.records.replace('writer_output_audit', recorded.model_copy(update={'raw_text': 'changed'}), expected_revision=1)
    assert service.records.get('writer_output_audit', audit['id'], WriterOutputAudit).raw_text == text


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['prefix', 'suffix', 'multiple', 'markdown', 'truncated', 'nested_fence', 'wrong_fence'])
async def test_invalid_writer_text_is_audited_without_fabricating_a_report(writer_boundary_context, invalid):
    payload = json.dumps(writer_boundary_context[3])
    text = {'prefix': 'Explanation: ' + payload, 'suffix': payload + '\nExplanation.',
            'multiple': payload + '\n' + payload, 'markdown': '# Plain report\nSensitive-output-marker',
            'truncated': payload[:-4], 'nested_fence': f'```json\n```json\n{payload}\n```\n```',
            'wrong_fence': f'```python\n{payload}\n```'}[invalid]
    service, model, run, extraction = writer_service(writer_boundary_context, text)
    with pytest.raises(ScholarError) as failure:
        await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert failure.value.code == 'invalid_model_output'
    assert 'writer_output_audit_id' in failure.value.details
    audit, = audit_rows(service)
    assert audit['raw_text'] == text
    assert audit['id'] == failure.value.details['writer_output_audit_id']
    assert audit['diagnostics'] == failure.value.details['diagnostics']
    assert audit['diagnostics'] and all(set(item) == {'loc', 'type'} for item in audit['diagnostics'])
    assert 'Sensitive-output-marker' not in str(failure.value) + str(failure.value.details)
    assert service.store.list(run.project_id) == []
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_writer_schema_diagnostics_locate_the_bad_field_without_its_value(writer_boundary_context):
    output = writer_boundary_context[3]
    output['blocks'][0]['text'] = {'private': 'Sensitive-output-marker'}
    text = json.dumps(output)
    service, _, run, extraction = writer_service(writer_boundary_context, text)
    with pytest.raises(ScholarError) as failure:
        await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert failure.value.details.get('diagnostics') == [{'loc': ['blocks', 0, 'text'], 'type': 'string_type'}]
    assert 'Sensitive-output-marker' not in str(failure.value) + str(failure.value.details)
    audit, = audit_rows(service)
    assert audit['raw_text'] == text
    assert audit['diagnostics'] == failure.value.details['diagnostics']


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['negative_start', 'negative_index', 'reversed', 'missing_end', 'wrong_index_type', 'non_object'])
async def test_invalid_generated_claim_schema_preserves_only_an_unverified_draft(writer_boundary_context, invalid):
    output = writer_boundary_context[3]
    claim = output['blocks'][0]['claims'][0]
    if invalid == 'negative_start':
        claim['start'] = -1
    elif invalid == 'negative_index':
        claim['token_index'] = -1
    elif invalid == 'reversed':
        claim.update(start=10, end=4)
    elif invalid == 'missing_end':
        del claim['end']
    elif invalid == 'wrong_index_type':
        claim['token_index'] = 'Sensitive-output-marker'
    else:
        output['blocks'][0]['claims'] = ['Sensitive-output-marker']
    text = json.dumps(output)
    service, model, run, extraction = writer_service(writer_boundary_context, text)
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert draft.blocks[0].text == output['blocks'][0]['text']
    assert draft.blocks[0].claims == []
    assert draft.manifest['writer_issues'][0]['code'] == 'invalid_claim_schema'
    assert 'Sensitive-output-marker' not in str(draft.manifest)
    audit, = audit_rows(service)
    assert audit['raw_text'] == text
    assert audit['diagnostics'] and all(item['loc'][:4] == ['blocks', 0, 'claims', 0] for item in audit['diagnostics'])
    assert all(set(item) == {'loc', 'type'} for item in audit['diagnostics'])
    saved = service.save(run.project_id, draft, 0)
    checked = await service.check(saved.id)
    assert checked.quality_status == 'issues_present'
    assert any(item['status'] == 'needs_recheck' for item in checked.checks)
    assert saved.occurrences[0].evidence_ids == []
    assert len(model.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_index', [0, '0', None])
async def test_invalid_claim_cannot_make_a_competing_binding_look_unique(writer_boundary_context, invalid_index):
    output = writer_boundary_context[3]
    block = output['blocks'][0]
    original_text = block['text']
    original_claim = block['claims'][0]
    second_start = len(original_text) + 1
    block['text'] += ' ' + original_text
    block['claims'] = [dict(original_claim),
                       dict(original_claim, token_index=1, start=second_start,
                            end=second_start + original_claim['end']),
                       dict(original_claim, token_index=invalid_index, start=-1)]
    service, model, run, extraction = writer_service(writer_boundary_context, json.dumps(output))
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert [claim['token_index'] for claim in draft.blocks[0].claims] == ([] if invalid_index is None else [1])
    assert draft.blocks[0].text == block['text']
    assert any(issue['code'] == 'invalid_claim_schema' for issue in draft.manifest['writer_issues'])
    saved = service.save(run.project_id, draft, 0)
    checked = await service.check(saved.id)
    assert saved.occurrences[0].evidence_ids == []
    assert checked.quality_status == 'issues_present'
    assert any(item['status'] == 'needs_recheck' for item in checked.checks)
    assert len(model.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['report', 'block', 'claim'])
async def test_schema_diagnostic_locations_redact_model_invented_field_names(writer_boundary_context, target):
    output = writer_boundary_context[3]
    node = {'report': output, 'block': output['blocks'][0],
            'claim': output['blocks'][0]['claims'][0]}[target]
    node['Sensitive-field-name-marker'] = 'Sensitive-field-value-marker'
    text = json.dumps(output)
    service, _, run, extraction = writer_service(writer_boundary_context, text)
    if target == 'claim':
        draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
        public = str(draft.manifest)
        diagnostics = draft.manifest['writer_issues'][0]['diagnostics']
    else:
        with pytest.raises(ScholarError) as failure:
            await service.compose(run.id, {'extraction_ids': [extraction.id]})
        public = str(failure.value) + str(failure.value.details)
        diagnostics = failure.value.details['diagnostics']
    assert 'Sensitive-field-name-marker' not in public
    assert 'Sensitive-field-value-marker' not in public
    assert diagnostics[-1]['loc'][-1] == '<extra_field>'
    assert diagnostics[-1]['type'] == 'extra_forbidden'
    audit, = audit_rows(service)
    assert audit['raw_text'] == text
    assert audit['diagnostics'] == diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize('unknown', ['citation_key', 'claim_id', 'evidence_id'])
async def test_writer_boundary_keeps_unknown_source_guards(writer_boundary_context, unknown):
    output = writer_boundary_context[3]
    block = output['blocks'][0]
    if unknown == 'citation_key':
        block['text'] = 'The method uses a graph. [@sf_not_an_input]'
    elif unknown == 'claim_id':
        block['claims'][0]['claim_revision_ids'] = ['not-an-input-claim']
    else:
        block['claims'][0]['evidence_ids'] = ['not-an-input-evidence']
    service, model, run, extraction = writer_service(writer_boundary_context, json.dumps(output))
    draft = await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert draft.blocks[0].text == block['text']
    if unknown == 'citation_key':
        with pytest.raises(ScholarError) as failure:
            service.save(run.project_id, draft, 0)
        assert failure.value.code == 'unknown_citation_key'
    else:
        assert draft.blocks[0].claims == []
        assert draft.manifest['writer_issues'][0]['code'] == 'invalid_claim_source'
        saved = service.save(run.project_id, draft, 0)
        assert saved.occurrences[0].evidence_ids == []
        assert (await service.check(saved.id)).quality_status == 'issues_present'
        # Canonical/API-style saves retain their strict source validation.
        unsafe = draft.model_copy(update={'blocks': [ReportBlock(**block)]})
        with pytest.raises(ScholarError) as failure:
            service.save(run.project_id, unsafe, saved.revision)
        assert failure.value.code == 'unknown_evidence'
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_api_report_save_still_rejects_negative_claim_offsets(api, writer_boundary_context):
    _, run, extraction, output = writer_boundary_context
    output['blocks'][0]['claims'][0]['start'] = -1
    response = await api.post(f'/api/v1/projects/{run.project_id}/reports', json={
        'expected_revision': 0, 'draft': {'run_id': run.id, **output, 'manifest': {'extraction_ids': [extraction.id]}}})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_no_writer_response_does_not_create_an_output_audit_or_report(writer_boundary_context):
    service, _, run, extraction = writer_service(writer_boundary_context, '')
    class DisconnectedWriter:
        async def complete(self, _):
            raise ConnectionError('Synthetic transport failure')
    service.gateway = DisconnectedWriter()
    with pytest.raises(ConnectionError):
        await service.compose(run.id, {'extraction_ids': [extraction.id]})
    assert audit_rows(service) == []
    assert service.store.list(run.project_id) == []
