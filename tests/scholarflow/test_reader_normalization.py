"""Reader normalization and failed-response recovery never invent source evidence."""
import hashlib
import json

import pytest
from pydantic import ValidationError

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.evidence import ReaderOutputAudit
from backend.scholarflow.domain.runs import Run
from backend.scholarflow.services.extractions import ExtractionService, ReaderOutput
from .test_extractions import extraction_context
from .test_documents import docs
from .test_library import library
from .test_reader_quality import Reader, anchored_payload, audit_rows


def legacy_missing_payload(inputs):
    result = anchored_payload([inputs['passages'][0]['quote_units'][0]['quote_id']])
    result['fields']['limitations'] = [{'value': 'Limitations are not stated in this supplied text.',
                                       'quote_ids': [], 'relation': 'missing'}]
    result['missing_reasons']['limitations'] = ['No explicit limitations are stated.', 'Only this source was checked.']
    return result


def test_reader_normalizes_only_explicit_unanchored_missing_items():
    result = anchored_payload(['Q0001'])
    result['fields']['limitations'] = [{'value': 'Not reported.', 'quote_ids': [], 'relation': 'missing'}]
    result['missing_reasons']['limitations'] = ['Not stated here.', 'Only this batch was checked.']
    output = ExtractionService._output(json.dumps(result), [{'quote_id': 'Q0001'}])
    assert output.fields.limitations == []
    assert output.missing_reasons['limitations'] == 'Not stated here.; Only this batch was checked.'
    assert output.fields.method[0].quote_ids == ['Q0001']
    assert output._normalizations == [
        {'loc': ['missing_reasons', 'limitations'], 'type': 'reason_list_joined'},
        {'loc': ['fields', 'limitations', 0], 'type': 'unanchored_missing_removed'}]


def test_reader_preserves_all_five_valid_anchors_without_truncation():
    ids = [f'Q{index:04d}' for index in range(1, 6)]
    output = ExtractionService._output(json.dumps(anchored_payload(ids)), [{'quote_id': item} for item in ids])
    assert output.fields.method[0].quote_ids == ids
    assert output._normalizations == []


@pytest.mark.parametrize('case', ['no_reason', 'blank_reason', 'mixed_reason', 'unknown_key', 'anchored_missing', 'unknown_id', 'invalid_relation_type', 'invalid_relation_list'])
def test_reader_does_not_normalize_away_invalid_or_unknown_sources(case):
    result = anchored_payload(['Q0001'])
    candidate = {'value': 'Not reported.', 'quote_ids': [], 'relation': 'missing'}
    result['fields']['limitations'] = [candidate]
    if case == 'no_reason':
        del result['missing_reasons']['limitations']
    elif case == 'blank_reason':
        result['missing_reasons']['limitations'] = '  '
    elif case == 'mixed_reason':
        result['missing_reasons']['limitations'] = ['Reason', {'untrusted': 'value'}]
    elif case == 'unknown_key':
        candidate['Sensitive-extra-marker'] = 'Sensitive-value-marker'
    elif case == 'anchored_missing':
        candidate['quote_ids'] = ['Q0001']
    elif case == 'invalid_relation_type':
        candidate['relation'] = {'Sensitive-relation-marker': 'missing'}
    elif case == 'invalid_relation_list':
        candidate['relation'] = ['missing']
    else:
        result['fields']['method'][0]['quote_ids'] = ['Q9999']
    with pytest.raises((ValidationError, ScholarError)):
        ExtractionService._output(json.dumps(result), [{'quote_id': 'Q0001'}])


async def seeded_failed_response(context, monkeypatch):
    documents, run, paper, document = context
    model = Reader([legacy_missing_payload])
    service = ExtractionService(documents.db, model)
    def reject_with_old_schema(raw, units):
        return ReaderOutput.model_validate({'fields': {}})
    with monkeypatch.context() as patch:
        patch.setattr(service, '_output', reject_with_old_schema)
        with pytest.raises(ScholarError) as failure:
            await service.extract(run.id, paper.paper_version_id, [document.id])
    assert failure.value.code == 'invalid_model_output'
    batch, = [item for item in service.runs.list_jobs(run.id) if item.stage == 'read_paper_batch']
    audit, = audit_rows(service)
    model.requests.clear()
    return service, model, run, paper, document, batch, audit


def retry_run(service, origin):
    run = Run(project_id=origin.project_id, request_snapshot=origin.request_snapshot)
    service.records.insert('run', run, project_id=run.project_id)
    return run


@pytest.mark.asyncio
async def test_reader_recovers_matching_failed_response_under_new_fenced_artifact(extraction_context, monkeypatch):
    service, model, origin, paper, document, source_job, source_audit = await seeded_failed_response(extraction_context, monkeypatch)
    run = retry_run(service, origin)
    extraction = await service.extract(run.id, paper.paper_version_id, [document.id])
    assert model.requests == []
    assert extraction.fields['method'].claim_ids and extraction.fields['method'].evidence_ids
    assert extraction.fields['limitations'].claim_ids == []
    assert extraction.fields['limitations'].evidence_ids == []
    assert extraction.fields['limitations'].value is None
    assert 'No explicit limitations' in extraction.fields['limitations'].missing_reason
    audits = audit_rows(service)
    new, = [item for item in audits if item['id'] != source_audit['id']]
    assert new['run_id'] == run.id and new['reused_from_audit_id'] == source_audit['id']
    assert new['raw_text'] == source_audit['raw_text'] and new['sha256'] == source_audit['sha256']
    assert new['diagnostics'] == [] and len(new['normalizations']) == 2
    assert service.runs.get_job(source_job.id).status == 'failed'
    assert service.records.get('reader_output_audit', source_audit['id'], ReaderOutputAudit).diagnostics
    current, = [item for item in service.runs.list_jobs(run.id) if item.stage == 'read_paper_batch']
    assert current.status == 'succeeded' and current.result['reader_output_audit_id'] == new['id']
    later = retry_run(service, origin)
    await service.extract(later.id, paper.paper_version_id, [document.id])
    assert model.requests == [] and len(audit_rows(service)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['orphan', 'raw_sha', 'wrong_model', 'wrong_prompt', 'wrong_document',
                                  'wrong_project', 'wrong_version', 'wrong_origin', 'cancelled_origin',
                                  'unknown_id', 'wrong_error', 'ambiguous_audits'])
async def test_reader_failed_response_recovery_requires_complete_provenance(extraction_context, monkeypatch, case):
    service, model, origin, paper, document, job, audit = await seeded_failed_response(extraction_context, monkeypatch)
    if case == 'cancelled_origin':
        service.runs.cancel_run(origin.id)
    elif case == 'wrong_origin':
        audit['run_id'] = retry_run(service, origin).id
    elif case == 'raw_sha':
        audit['sha256'] = 'mismatched-raw-hash'
    elif case == 'wrong_document':
        audit['document_version_ids'] = ['other-document']
    elif case == 'wrong_project':
        audit['project_id'] = 'other-project'
    elif case == 'wrong_version':
        audit['reader_version'] = 'reader-v3'
    elif case == 'unknown_id':
        raw = json.loads(audit['raw_text'])
        raw['fields']['method'][0]['quote_ids'] = ['Q9999']
        audit['raw_text'] = json.dumps(raw)
        audit['sha256'] = hashlib.sha256(audit['raw_text'].encode()).hexdigest()
    elif case == 'ambiguous_audits':
        from uuid import uuid4
        duplicate = ReaderOutputAudit.model_validate(dict(audit, id=str(uuid4())))
        service.records.insert('reader_output_audit', duplicate, project_id=origin.project_id)
    with service.db.transaction() as conn:
        if case == 'orphan':
            conn.execute('DELETE FROM jobs WHERE id=?', (job.id,))
        elif case in {'wrong_model', 'wrong_prompt'}:
            row = conn.execute('SELECT payload FROM jobs WHERE id=?', (job.id,)).fetchone()
            stored = json.loads(row['payload'])
            if case == 'wrong_model':
                stored['manifest']['model'] = {'provider': 'wrong-model'}
            else:
                stored['manifest']['prompt_sha256'] = 'wrong-prompt'
            conn.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(stored), job.id))
        elif case == 'wrong_error':
            conn.execute('UPDATE jobs SET error=? WHERE id=?', (json.dumps({'code': 'operation_failed'}), job.id))
        else:
            conn.execute('UPDATE entities SET payload=? WHERE id=?', (json.dumps(audit), audit['id']))
    run = retry_run(service, origin)
    await service.extract(run.id, paper.paper_version_id, [document.id])
    assert len(model.requests) == 1
    new = [item for item in audit_rows(service) if item['run_id'] == run.id]
    assert len(new) == 1 and new[0].get('reused_from_audit_id') is None


@pytest.mark.asyncio
async def test_reader_recovered_response_cannot_publish_after_lease_loss(extraction_context, monkeypatch):
    service, model, origin, paper, document, _, _ = await seeded_failed_response(extraction_context, monkeypatch)
    run = retry_run(service, origin)
    monkeypatch.setattr(service.runs, 'finish', lambda *args, **kwargs: False)
    with pytest.raises(ScholarError) as failure:
        await service.extract(run.id, paper.paper_version_id, [document.id])
    assert failure.value.code == 'lease_lost'
    assert model.requests == []
    assert service.latest(run.project_id, paper.paper_version_id) is None
    assert not any(item.status == 'succeeded' for item in service.runs.list_jobs(run.id))
