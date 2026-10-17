"""An explicit local source review can complete a claim-free frozen batch."""
import hashlib
import json

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.evidence import ClaimRevision, ReaderOutputAudit
from backend.scholarflow.domain.projects import Project
from backend.scholarflow.domain.runs import Run
from backend.scholarflow.services.extractions import ExtractionService
from backend.scholarflow.persistence.run_store import fingerprint
from .test_extractions import extraction_context
from .test_documents import docs
from .test_library import library
from .test_reader_quality import Reader, FIELDS, anchored_payload, audit_rows


REASONS = {name: 'The reviewed batch contains only references, not this paper\'s own facts.' for name in FIELDS}


async def review_context(context):
    documents, origin, paper, document = context
    model = Reader([anchored_payload(['Q9999'])])
    service = ExtractionService(documents.db, model)
    with pytest.raises(ScholarError) as error:
        await service.extract(origin.id, paper.paper_version_id, [document.id])
    assert error.value.code == 'unknown_quote_id'
    source, = [job for job in service.runs.list_jobs(origin.id) if job.stage == 'read_paper_batch']
    carrier = Run(project_id=origin.project_id, status='running', stage='source_review',
                  request_snapshot=origin.request_snapshot)
    service.records.insert('run', carrier, project_id=carrier.project_id)
    model.requests.clear()
    return service, model, origin, carrier, paper, document, source


async def review(service, carrier, source, **changes):
    args = dict(expected_input_hash=source.input_hash, reviewer='fixture_source_review',
                review_reason='The two frozen source pages were checked and contain only references.',
                missing_reasons=REASONS)
    args.update(changes)
    return await service.review_source_batch(carrier.id, source.id, **args)


@pytest.mark.asyncio
async def test_source_review_creates_new_fenced_empty_batch_without_model_or_old_mutation(extraction_context):
    service, model, origin, carrier, paper, document, source = await review_context(extraction_context)
    original_audit, = audit_rows(service)
    result = await review(service, carrier, source)
    assert model.requests == []
    saved = service.records.get('reader_output_audit', result['reader_output_audit_id'], ReaderOutputAudit)
    assert saved.origin == 'source_review' and saved.reviewer == 'fixture_source_review'
    assert saved.review_reason.startswith('The two frozen source pages')
    assert saved.run_id == carrier.id and saved.input_hash == source.input_hash
    assert saved.provider_request_id is None and saved.usage is None
    assert saved.diagnostics == [] and saved.normalizations == [] and saved.reused_from_audit_id is None
    raw = json.loads(saved.raw_text)
    assert raw == {'fields': {name: [] for name in FIELDS}, 'missing_reasons': REASONS}
    assert saved.sha256 == hashlib.sha256(saved.raw_text.encode()).hexdigest()
    current = service.runs.get_job(result['batch_job_id'])
    assert current.run_id == carrier.id and current.id != source.id and current.status == 'succeeded'
    assert current.input_manifest == source.input_manifest
    assert service.runs.get_job(source.id) == source
    assert service.records.get('reader_output_audit', original_audit['id'], ReaderOutputAudit).raw_text == original_audit['raw_text']
    assert service.latest(origin.project_id, paper.paper_version_id) is None
    assert service.records.list('claim', ClaimRevision) == []
    # The normal Reader path can reuse this success, but an entirely empty paper
    # still fails rather than fabricating a successful extraction.
    retry = Run(project_id=origin.project_id, request_snapshot=origin.request_snapshot)
    service.records.insert('run', retry, project_id=retry.project_id)
    with pytest.raises(ScholarError) as error:
        await service.extract(retry.id, paper.paper_version_id, [document.id])
    assert error.value.code == 'empty_extraction' and model.requests == []


@pytest.mark.asyncio
async def test_normal_reading_reuses_prior_success_and_source_review_to_finish_paper(extraction_context):
    documents, origin, paper, document = extraction_context
    model = Reader([lambda inputs: anchored_payload([inputs['passages'][0]['quote_units'][0]['quote_id']]),
                    anchored_payload(['Q9999'])])
    service = ExtractionService(documents.db, model, batch_chars=32)
    with pytest.raises(ScholarError):
        await service.extract(origin.id, paper.paper_version_id, [document.id])
    source, = [job for job in service.runs.list_jobs(origin.id) if job.stage == 'read_paper_batch' and job.status == 'failed']
    carrier = Run(project_id=origin.project_id, status='running', stage='source_review', request_snapshot=origin.request_snapshot)
    service.records.insert('run', carrier, project_id=carrier.project_id)
    model.requests.clear()
    reviewed = await review(service, carrier, source)
    retry = Run(project_id=origin.project_id, request_snapshot=origin.request_snapshot)
    service.records.insert('run', retry, project_id=retry.project_id)
    extraction = await service.extract(retry.id, paper.paper_version_id, [document.id])
    assert model.requests == [] and extraction.coverage['complete'] is True
    assert len(extraction.fields['method'].claim_ids) == 1
    assert reviewed['reader_output_audit_id'] in extraction.coverage['reader_output_audit_ids']
    assert len(service.records.list('claim', ClaimRevision)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['wrong_hash', 'wrong_model', 'wrong_document', 'wrong_fragment', 'wrong_units',
                                  'wrong_project', 'cancelled_origin', 'cancelled_carrier', 'same_run',
                                  'blank_reviewer', 'blank_review_reason', 'missing_field_reason'])
async def test_source_review_rejects_unfrozen_inputs_and_invalid_review_metadata(extraction_context, case):
    service, model, origin, carrier, paper, document, source = await review_context(extraction_context)
    changes = {}
    if case == 'wrong_hash':
        changes['expected_input_hash'] = '0' * 64
    elif case == 'wrong_project':
        project = service.records.get('project', origin.project_id, Project)
        service.records.insert('project', project.model_copy(update={'id': 'other-project'}))
        other = carrier.model_copy(update={'id': 'other-carrier', 'project_id': 'other-project'})
        service.records.insert('run', other, project_id=other.project_id)
        carrier = other
    elif case == 'cancelled_origin':
        service.runs.cancel_run(origin.id)
    elif case == 'cancelled_carrier':
        service.runs.cancel_run(carrier.id)
    elif case == 'same_run':
        carrier = origin
    elif case == 'blank_reviewer':
        changes['reviewer'] = '   '
    elif case == 'blank_review_reason':
        changes['review_reason'] = ''
    elif case == 'missing_field_reason':
        changes['missing_reasons'] = {name: value for name, value in REASONS.items() if name != 'dataset'}
    else:
        with service.db.transaction() as conn:
            value = json.loads(conn.execute('SELECT payload FROM jobs WHERE id=?', (source.id,)).fetchone()['payload'])
            manifest = value['manifest']
            if case == 'wrong_model':
                manifest['model'] = {'model': 'another-model'}
            elif case == 'wrong_document':
                manifest['document_ids'] = ['another-document']
            elif case == 'wrong_fragment':
                manifest['fragments'][0]['sha256'] = 'wrong-fragment-sha'
            else:
                manifest['quote_units'][0]['start'] += 1
            digest = fingerprint(manifest)
            changes['expected_input_hash'] = digest
            conn.execute('UPDATE jobs SET payload=?,input_hash=? WHERE id=?', (json.dumps(value), digest, source.id))
    with pytest.raises(ScholarError):
        await review(service, carrier, source, **changes)
    assert model.requests == [] and len(audit_rows(service)) == 1
    assert service.runs.get_job(source.id).status == 'failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['claims', 'provider', 'usage', 'reviewer', 'review_reason', 'missing_reason'])
async def test_source_review_cache_revalidates_empty_claims_and_provenance(extraction_context, case):
    service, model, origin, carrier, paper, document, source = await review_context(extraction_context)
    result = await review(service, carrier, source)
    with service.db.transaction() as conn:
        value = json.loads(conn.execute('SELECT payload FROM entities WHERE id=?', (result['reader_output_audit_id'],)).fetchone()['payload'])
        if case in {'claims', 'missing_reason'}:
            raw = json.loads(value['raw_text'])
            if case == 'claims':
                raw['fields']['method'] = anchored_payload(['Q0001'])['fields']['method']
            else:
                raw['missing_reasons'].pop('dataset')
            value['raw_text'] = json.dumps(raw)
            value['sha256'] = hashlib.sha256(value['raw_text'].encode()).hexdigest()
        elif case == 'provider':
            value['provider_request_id'] = 'fake-model-request'
        elif case == 'usage':
            value['usage'] = {'output_tokens': 1}
        else:
            value[case] = None
        conn.execute('UPDATE entities SET payload=? WHERE id=?', (json.dumps(value), value['id']))
    retry = Run(project_id=origin.project_id, request_snapshot=origin.request_snapshot)
    service.records.insert('run', retry, project_id=retry.project_id)
    with pytest.raises(ScholarError) as failure:
        await service.extract(retry.id, paper.paper_version_id, [document.id])
    assert failure.value.code == 'invalid_reader_batch_cache'
    assert model.requests == [] and service.latest(origin.project_id, paper.paper_version_id) is None


@pytest.mark.asyncio
async def test_source_review_lease_loss_leaves_only_unpublished_audit(extraction_context, monkeypatch):
    service, model, origin, carrier, paper, document, source = await review_context(extraction_context)
    monkeypatch.setattr(service.runs, 'finish', lambda *args, **kwargs: False)
    with pytest.raises(ScholarError) as failure:
        await review(service, carrier, source)
    assert failure.value.code == 'lease_lost'
    assert model.requests == [] and service.latest(origin.project_id, paper.paper_version_id) is None
    assert not any(job.status == 'succeeded' for job in service.runs.list_jobs(carrier.id))
