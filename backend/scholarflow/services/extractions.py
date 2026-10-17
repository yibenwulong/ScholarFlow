import hashlib
import json
import re
from uuid import uuid4
from typing import Literal

from pydantic import Field, PrivateAttr, ValidationError, field_validator, model_validator

from ..domain.common import Model, utcnow
from ..domain.errors import ScholarError, revision_conflict
from ..domain.evidence import ClaimRevision, Evidence, ExtractionRevision, ExtractedField, ReaderOutputAudit
from ..domain.library import PaperVersion
from ..domain.projects import Project
from ..persistence.evidence_store import EvidenceStore
from ..persistence.paper_store import PaperStore
from ..persistence.run_store import RunStore, fingerprint
from ..workflows.prompts import FIELD_NAMES, READ_PROMPT
from ..worker.jobs import ArtifactExecutor
from .model_gateway import ModelRequest, ModelResponse
from .reader_contract import READER_VERSION, reader_inputs
from .reader_sources import quote_units


class Candidate(Model):
    value: str = Field(min_length=1)
    quote_ids: list[str] = Field(min_length=1)
    relation: Literal['supports', 'refutes', 'insufficient'] = 'supports'

    @field_validator('value')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('A claim must contain text')
        return value

    @field_validator('quote_ids')
    @classmethod
    def unique_anchors(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Quote IDs must be unique')
        return value


class ReaderFields(Model):
    research_question: list[Candidate] = Field(max_length=2)
    method: list[Candidate] = Field(max_length=2)
    dataset: list[Candidate] = Field(max_length=2)
    findings: list[Candidate] = Field(max_length=2)
    limitations: list[Candidate] = Field(max_length=2)


class ReaderOutput(Model):
    fields: ReaderFields
    missing_reasons: dict[str, str] = Field(default_factory=dict)
    _normalizations: list[dict] = PrivateAttr(default_factory=list)

    @model_validator(mode='after')
    def checked_fields(self):
        if set(self.missing_reasons) - set(FIELD_NAMES):
            raise ValueError('Unknown missing-reason field')
        for name in FIELD_NAMES:
            if not getattr(self.fields, name) and not self.missing_reasons.get(name, '').strip():
                raise ValueError('Every empty field needs an explicit missing reason')
        return self


def reader_json_text(raw):
    text = raw.strip().removeprefix('\ufeff').strip()
    fenced = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', text, flags=re.DOTALL | re.IGNORECASE)
    return (fenced.group(1), 'json_fence') if fenced else (text, 'json')


def reader_diagnostics(error):
    known = set(FIELD_NAMES) | {'fields', 'missing_reasons', 'value', 'quote_ids', 'relation'}
    return [{'loc': [part if isinstance(part, int) or part in known else '<extra_field>' for part in item['loc']],
             'type': item['type']} for item in error.errors(include_input=False, include_url=False, include_context=False)]


def normalize_reader_payload(value):
    """Only repair explicit missing-data representations, never evidence or facts."""
    changes = []
    if not isinstance(value, dict):
        return value, changes
    reasons = value.get('missing_reasons')
    if not isinstance(reasons, dict):
        return value, changes
    for name in FIELD_NAMES:
        reason = reasons.get(name)
        if isinstance(reason, list) and reason and all(isinstance(part, str) and part.strip() for part in reason):
            reasons[name] = '; '.join(part.strip() for part in reason)
            changes.append({'loc': ['missing_reasons', name], 'type': 'reason_list_joined'})
    fields = value.get('fields')
    if not isinstance(fields, dict):
        return value, changes
    for name in FIELD_NAMES:
        items, reason = fields.get(name), reasons.get(name)
        if (not isinstance(items, list) or len(items) > 2
                or not isinstance(reason, str) or not reason.strip()):
            continue
        kept = []
        for index, item in enumerate(items):
            if (isinstance(item, dict) and set(item) <= {'value', 'quote_ids', 'relation'}
                    and isinstance(item.get('value'), str) and item['value'].strip()
                    and item.get('quote_ids') == []
                    and isinstance(item.get('relation', 'supports'), str)
                    and item.get('relation', 'supports') in {'supports', 'refutes', 'insufficient', 'missing'}):
                changes.append({'loc': ['fields', name, index], 'type': 'unanchored_missing_removed'})
            else:
                kept.append(item)
        fields[name] = kept
    return value, changes


class ExtractionService:
    def __init__(self, db, gateway, *, batch_chars=18000):
        if batch_chars < 1:
            raise ValueError('batch_chars must be positive')
        self.db = db
        self.gateway = gateway
        self.store = EvidenceStore(db)
        self.records = self.store.records
        self.runs = RunStore(db)
        self.batch_chars = batch_chars
        self.batch_executor = ArtifactExecutor(self.runs)

    def get(self, extraction_id):
        return self.records.get('extraction', extraction_id, ExtractionRevision)

    def latest(self, project_id, paper_version_id, conn=None):
        items = [e for e in self.records.list('extraction', ExtractionRevision, project_id=project_id, conn=conn)
                 if e.paper_version_id == paper_version_id]
        return max(items, key=lambda e: (e.revision, e.created_at, e.id)) if items else None

    @staticmethod
    def _output(raw, units):
        text, _ = reader_json_text(raw)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            # Preserve Pydantic's existing safe JSON diagnostics.
            return ReaderOutput.model_validate_json(text)
        value, normalizations = normalize_reader_payload(value)
        output = ReaderOutput.model_validate(value)
        output._normalizations = normalizations
        allowed = {unit['quote_id'] for unit in units}
        for name in FIELD_NAMES:
            for index, candidate in enumerate(getattr(output.fields, name)):
                for position, quote_id in enumerate(candidate.quote_ids):
                    if quote_id not in allowed:
                        raise ScholarError(422, 'unknown_quote_id', '阅读结果引用了本批次未提供的引文锚点，无法保存；请检查阅读输出。',
                            details={'diagnostics': [{'loc': ['fields', name, index, 'quote_ids', position],
                                                       'type': 'unknown_quote_id'}]})
        return output

    def _batch_result(self, job, manifest, units):
        digest = fingerprint(manifest)
        try:
            if (job is None or job.kind != 'artifact' or job.stage != 'read_paper_batch'
                    or job.status != 'succeeded' or job.input_hash != digest or job.input_manifest != manifest
                    or not isinstance(job.result, dict)):
                raise ValueError('No matching fenced batch')
            audit = self.records.get('reader_output_audit', job.result.get('reader_output_audit_id'), ReaderOutputAudit)
            owner = self.runs.get_run(job.run_id)
            origin = self.runs.get_run(audit.run_id)
            if (owner.project_id != manifest['_project_id'] or origin.project_id != owner.project_id
                    or audit.project_id != owner.project_id or audit.paper_version_id != manifest['paper_version_id']
                    or audit.document_version_ids != manifest['document_ids'] or audit.input_hash != digest
                    or audit.reader_version != READER_VERSION or audit.diagnostics
                    or audit.sha256 != hashlib.sha256(audit.raw_text.encode('utf-8')).hexdigest()):
                raise ValueError('Mismatched audit provenance')
            output = self._output(audit.raw_text, units)
            if [item.model_dump() for item in audit.normalizations] != output._normalizations:
                raise ValueError('Mismatched normalization metadata')
            if audit.origin == 'source_review' and (output._normalizations
                    or any(getattr(output.fields, name) for name in FIELD_NAMES)
                    or set(output.missing_reasons) != set(FIELD_NAMES)):
                raise ValueError('A source review must explicitly leave all five fields empty')
            return output, audit.id, job.id
        except (ScholarError, ValidationError, ValueError, TypeError):
            raise ScholarError(409, 'invalid_reader_batch_cache', 'The saved Reader batch does not match its frozen input') from None

    def _recoverable_response(self, manifest, units):
        """A failed fenced job, not an isolated raw audit, authorizes revalidation."""
        digest = fingerprint(manifest)
        with self.db.transaction(write=False) as conn:
            jobs = conn.execute("SELECT j.id FROM jobs j JOIN entities r ON r.id=j.run_id "
                "WHERE j.kind='artifact' AND j.stage='read_paper_batch' AND j.status='failed' "
                "AND j.input_hash=? AND r.kind='run' AND r.project_id=? ORDER BY j.updated_at DESC,j.id DESC",
                (digest, manifest['_project_id'])).fetchall()
            if not jobs:
                return None
            audits = conn.execute("SELECT payload FROM entities WHERE kind='reader_output_audit' AND project_id=? "
                "ORDER BY created_at DESC,id DESC", (manifest['_project_id'],)).fetchall()
            for row in jobs:
                job = self.runs.get_job(row['id'], conn)
                if job.input_manifest != manifest or not job.error or job.error.get('code') != 'invalid_model_output':
                    continue
                origin = self.runs.get_run(job.run_id, conn)
                if (origin.status == 'cancelled' or origin.project_id != manifest['_project_id']
                        or origin.request_snapshot.get('model', {}) != manifest['model']):
                    continue
                linked = []
                for row in audits:
                    value = json.loads(row['payload'])
                    if value.get('run_id') == job.run_id and value.get('input_hash') == digest:
                        linked.append(value)
                # Historical job errors did not retain an audit ID. Multiple
                # attempts with the same origin/hash cannot be disambiguated.
                if len(linked) != 1:
                    continue
                try:
                    audit = ReaderOutputAudit.model_validate(linked[0])
                    if (audit.project_id != origin.project_id or audit.paper_version_id != manifest['paper_version_id']
                            or audit.document_version_ids != manifest['document_ids']
                            or audit.reader_version != READER_VERSION or audit.origin != 'model' or not audit.diagnostics
                            or audit.reused_from_audit_id is not None
                            or audit.sha256 != hashlib.sha256(audit.raw_text.encode('utf-8')).hexdigest()):
                        continue
                    self._output(audit.raw_text, units)
                except (ScholarError, ValidationError, ValueError, TypeError):
                    continue
                return audit, job
        return None

    @staticmethod
    def _batch_manifest(run, project, paper_version_id, documents, batch_index, batch, paper_title):
        units = quote_units(batch)
        manifest = reader_inputs({'project': project.model_dump(mode='json'), 'paper_version_id': paper_version_id,
            'paper_title': paper_title,
            'document_ids': [document.id for document in documents],
            'document_hashes': {document.id: document.sha256 for document in documents},
            'model': run.request_snapshot.get('model', {}),
            'prompt_sha256': hashlib.sha256(READ_PROMPT.encode('utf-8')).hexdigest(), 'batch_index': batch_index,
            'fragments': [{**{key: value for key, value in fragment.items() if key != 'text'},
                           'sha256': hashlib.sha256(fragment['text'].encode('utf-8')).hexdigest()} for fragment in batch],
            'quote_units': [{**{key: value for key, value in unit.items() if key != 'text'},
                             'sha256': hashlib.sha256(unit['text'].encode('utf-8')).hexdigest()} for unit in units],
            '_project_id': project.id})
        return manifest, units

    def _source_review_inputs(self, run_id, source_job_id, expected_input_hash):
        try:
            run = self.runs.get_run(run_id)
            source = self.runs.get_job(source_job_id)
            origin = self.runs.get_run(source.run_id)
            frozen = source.input_manifest
            if (source.kind != 'artifact' or source.stage != 'read_paper_batch' or source.status != 'failed'
                    or run.id == origin.id or run.status in {'cancelled', 'completed'} or origin.status == 'cancelled'
                    or run.project_id != origin.project_id or frozen.get('_project_id') != run.project_id
                    or source.input_hash != expected_input_hash or fingerprint(frozen) != expected_input_hash
                    or frozen.get('reader_version') != READER_VERSION
                    or type(frozen.get('batch_index')) is not int or frozen['batch_index'] < 0):
                raise ValueError('No matching failed source batch')
            project = Project.model_validate(frozen['project'])
            if (project.id != run.project_id
                    or run.request_snapshot.get('project', frozen['project']) != frozen['project']
                    or origin.request_snapshot.get('model', {}) != frozen['model']):
                raise ValueError('Mismatched frozen project or model')
            version = self.records.get('paper_version', frozen['paper_version_id'], PaperVersion)
            PaperStore(self.db).association(project.id, version.paper_id)
            document_ids = frozen['document_ids']
            if not isinstance(document_ids, list) or not document_ids or len(set(document_ids)) != len(document_ids):
                raise ValueError('Invalid document selection')
            documents = [self.store.document(document_id) for document_id in document_ids]
            if any(document.paper_version_id != version.id or document.parse_status != 'ready' for document in documents):
                raise ValueError('Mismatched source documents')
            passages = {passage.id: passage for document in documents for passage in self.store.passages(document.id)}
            kinds = {document.id: document.kind for document in documents}
            batch = []
            for fragment in frozen['fragments']:
                passage = passages[fragment['passage_id']]
                start, end = fragment['start'], fragment['end']
                if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(passage.text):
                    raise ValueError('Invalid original source offsets')
                batch.append({'passage_id': passage.id, 'document_version_id': passage.document_version_id,
                    'source_scope': 'abstract' if kinds[passage.document_version_id] == 'abstract' else 'fulltext',
                    'page_number': passage.page_number, 'start': start, 'end': end, 'text': passage.text[start:end]})
            if not batch:
                raise ValueError('No source fragments')
            manifest, units = self._batch_manifest(run, project, version.id, documents, frozen['batch_index'], batch, version.title)
            if manifest != frozen or fingerprint(manifest) != expected_input_hash:
                raise ValueError('Source text or unit map does not match the frozen batch')
            return run, source, manifest, units
        except (ScholarError, ValidationError, ValueError, TypeError, KeyError):
            raise ScholarError(409, 'invalid_reader_source_review', '来源复核必须对应同项目、同输入且可验证原文位置的失败阅读批次。') from None

    async def review_source_batch(self, run_id, source_job_id, *, expected_input_hash, reviewer, review_reason, missing_reasons):
        """Record an explicit local, claim-free review under a NEW batch lease.

        This internal entry point never detects reference sections automatically,
        invokes the gateway, publishes an extraction, or changes the old failure.
        """
        if (not isinstance(reviewer, str) or not reviewer.strip()
                or not isinstance(review_reason, str) or not review_reason.strip()
                or not isinstance(missing_reasons, dict) or set(missing_reasons) != set(FIELD_NAMES)
                or any(not isinstance(reason, str) or not reason.strip() for reason in missing_reasons.values())):
            raise ScholarError(422, 'invalid_reader_source_review', '来源复核需要复核者、复核说明和五个字段各自明确的缺失原因。')
        run, source, manifest, units = self._source_review_inputs(run_id, source_job_id, expected_input_hash)
        raw = json.dumps({'fields': {name: [] for name in FIELD_NAMES}, 'missing_reasons': missing_reasons},
                         ensure_ascii=False, sort_keys=True)
        cached = self.runs.cached_artifact('read_paper_batch', expected_input_hash, run.project_id)
        if cached:
            _, audit_id, _ = self._batch_result(cached, manifest, units)
            previous = self.records.get('reader_output_audit', audit_id, ReaderOutputAudit)
            if previous.origin != 'source_review':
                raise ScholarError(409, 'reader_source_review_conflict', '该批次已有模型阅读结果，请先检查现有结果再决定如何复核。')

        async def operation():
            # Repeat source validation inside the new active artifact execution.
            self._source_review_inputs(run_id, source_job_id, expected_input_hash)
            audit = ReaderOutputAudit(project_id=run.project_id, run_id=run.id,
                paper_version_id=manifest['paper_version_id'], document_version_ids=manifest['document_ids'],
                input_hash=expected_input_hash, reader_version=READER_VERSION, raw_text=raw,
                sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(), output_format='json',
                origin='source_review', reviewer=reviewer, review_reason=review_reason)
            with self.db.transaction() as conn:
                current, original = self.runs.get_run(run.id, conn), self.runs.get_job(source.id, conn)
                origin = self.runs.get_run(original.run_id, conn)
                if (current.status == 'cancelled' or origin.status == 'cancelled' or original.status != 'failed'
                        or original.input_manifest != manifest or original.input_hash != expected_input_hash):
                    raise ScholarError(409, 'reader_source_review_changed', '复核期间任务状态已变化，本次来源复核未保存。')
                self.records.insert('reader_output_audit', audit, project_id=run.project_id,
                    links=[('run', run.id), ('version', manifest['paper_version_id']), ('source_run', source.run_id)]
                          + [('document', document_id) for document_id in manifest['document_ids']], conn=conn)
            return {'reader_output_audit_id': audit.id}

        await self.batch_executor.execute(run.id, 'read_paper_batch', manifest, operation)
        job = next((job for job in self.runs.list_jobs(run.id) if job.kind == 'artifact'
                    and job.stage == 'read_paper_batch' and job.input_hash == expected_input_hash), None)
        _, audit_id, job_id = self._batch_result(job, manifest, units)
        saved = self.records.get('reader_output_audit', audit_id, ReaderOutputAudit)
        if (saved.origin != 'source_review' or saved.raw_text != raw
                or saved.reviewer != reviewer or saved.review_reason != review_reason):
            raise ScholarError(409, 'reader_source_review_conflict', '已有复核记录与本次明确提交的复核内容不同。')
        return {'reader_output_audit_id': audit_id, 'batch_job_id': job_id}

    async def _read_batch(self, run, project, paper_version_id, documents, batch_index, batch, paper_title):
        manifest, units = self._batch_manifest(run, project, paper_version_id, documents, batch_index, batch, paper_title)
        payload_passages = [{**{key: value for key, value in fragment.items() if key != 'text'},
            'quote_units': [{'quote_id': unit['quote_id'], 'text': unit['text']}
                            for unit in units if unit['fragment_index'] == index]}
            for index, fragment in enumerate(batch)]
        digest = fingerprint(manifest)
        cached = self.runs.cached_artifact('read_paper_batch', digest, project.id)
        if cached is not None:
            self._batch_result(cached, manifest, units)

        async def operation():
            recovered = self._recoverable_response(manifest, units)
            source = recovered[0] if recovered else None
            response = (ModelResponse(text=source.raw_text, provider_request_id=source.provider_request_id, usage=source.usage)
                if source else await self.gateway.complete(ModelRequest(run_id=run.id, purpose='read_paper', messages=[
                    {'role': 'system', 'content': READ_PROMPT}, {'role': 'user', 'content': json.dumps({
                        'paper_title': paper_title, 'language': project.language, 'passages': payload_passages}, ensure_ascii=False)}])))
            _, output_format = reader_json_text(response.text)
            failure, diagnostics, normalizations = None, [], []
            try:
                output = self._output(response.text, units)
                normalizations = output._normalizations
            except ValidationError as error:
                diagnostics = reader_diagnostics(error)
                failure = ('invalid_model_output', 'Reader returned invalid structured fields; review this paper')
            except ScholarError as error:
                diagnostics = error.details['diagnostics']
                failure = (error.code, error.message)
            audit = ReaderOutputAudit(project_id=project.id, run_id=run.id, paper_version_id=paper_version_id,
                document_version_ids=manifest['document_ids'], input_hash=digest, reader_version=READER_VERSION, raw_text=response.text,
                sha256=hashlib.sha256(response.text.encode('utf-8')).hexdigest(),
                provider_request_id=response.provider_request_id, usage=response.usage,
                output_format=output_format, diagnostics=diagnostics, normalizations=normalizations,
                reused_from_audit_id=source.id if source else None)
            with self.db.transaction() as conn:
                if recovered:
                    original = self.runs.get_job(recovered[1].id, conn)
                    origin = self.runs.get_run(source.run_id, conn)
                    if (original.status != 'failed' or original.run_id != source.run_id
                            or original.input_hash != digest or original.input_manifest != manifest
                            or not original.error or original.error.get('code') != 'invalid_model_output'
                            or origin.status == 'cancelled' or origin.project_id != project.id):
                        raise ScholarError(409, 'reader_recovery_unavailable', '原阅读任务状态已变化，本次未复用旧响应；请重新检查阅读状态。')
                self.records.insert('reader_output_audit', audit, project_id=project.id,
                    links=[('run', run.id), ('version', paper_version_id)] + [('document', document.id) for document in documents]
                          + ([('reader_output_audit', source.id)] if source else []), conn=conn)
            if failure:
                raise ScholarError(422, failure[0], failure[1],
                    details={'reader_output_audit_id': audit.id, 'diagnostics': diagnostics}) from None
            return {'reader_output_audit_id': audit.id}

        try:
            await self.batch_executor.execute(run.id, 'read_paper_batch', manifest, operation)
        except ScholarError as error:
            if error.retryable and error.code not in {'lease_lost', 'run_cancelled', 'model_budget_exhausted'}:
                # The batch executor already exhausted its three attempts. The
                # outer paper job must not multiply that into another 3x loop.
                raise ScholarError(error.status, error.code, error.message,
                    details={'batch_retry_exhausted': True}) from None
            raise
        job = next((item for item in self.runs.list_jobs(run.id)
                    if item.stage == 'read_paper_batch' and item.kind == 'artifact' and item.input_hash == digest), None)
        return (*self._batch_result(job, manifest, units), units)

    async def extract(self, run_id, paper_version_id, document_ids):
        run = self.runs.get_run(run_id)
        project = Project.model_validate(run.request_snapshot['project']) if 'project' in run.request_snapshot else self.records.get('project', run.project_id, Project)
        version = self.records.get('paper_version', paper_version_id, PaperVersion)
        PaperStore(self.db).association(project.id, version.paper_id)
        if not document_ids:
            raise ScholarError(422, 'missing_document', 'Choose a parsed PDF or abstract before reading')
        documents = [self.store.document(document_id) for document_id in dict.fromkeys(document_ids)]
        if any(d.paper_version_id != paper_version_id for d in documents):
            raise ScholarError(422, 'wrong_document_version', 'Document belongs to another paper version')
        if any(d.parse_status != 'ready' for d in documents):
            raise ScholarError(422, 'document_not_ready', 'A selected document has no usable parsed text')
        passages = {p.id: p for d in documents for p in self.store.passages(d.id)}
        kinds = {d.id: d.kind for d in documents}
        fragments = []
        for passage in passages.values():
            for start in range(0, len(passage.text), self.batch_chars):
                end = min(start + self.batch_chars, len(passage.text))
                fragments.append({'passage_id': passage.id, 'document_version_id': passage.document_version_id,
                    'source_scope': 'abstract' if kinds[passage.document_version_id] == 'abstract' else 'fulltext',
                    'page_number': passage.page_number, 'start': start, 'end': end, 'text': passage.text[start:end]})
        batches, batch, size = [], [], 0
        for fragment in fragments:
            if batch and size + len(fragment['text']) > self.batch_chars:
                batches.append(batch)
                batch, size = [], 0
            batch.append(fragment)
            size += len(fragment['text'])
        if batch:
            batches.append(batch)
        candidates, audits, batch_jobs = [], [], []
        missing = {name: [] for name in FIELD_NAMES}
        for batch_index, batch in enumerate(batches):
            output, audit_id, job_id, units = await self._read_batch(run, project, paper_version_id, documents, batch_index, batch, version.title)
            audits.append(audit_id)
            batch_jobs.append(job_id)
            for name in FIELD_NAMES:
                values = getattr(output.fields, name)
                candidates.extend((name, candidate, {unit['quote_id']: unit for unit in units}) for candidate in values)
                if not values:
                    missing[name].append(output.missing_reasons[name].strip())
        if not candidates:
            raise ScholarError(422, 'empty_extraction',
                ('本次阅读未提取到可用证据，不能视为阅读成功。请检查原文文本或补充全文后再补读；相同输入会复用已检查的批次。'
                 if project.language.lower().startswith('zh') else
                 'Reader returned no extractable evidence; inspect or supplement the source text before rereading. Identical inputs reuse checked batches.'),
                details={'reader_output_audit_ids': audits, 'reader_version': READER_VERSION})
        abstract_only = all(document.kind == 'abstract' for document in documents)
        fields = {}
        for name in FIELD_NAMES:
            reason = '; '.join(dict.fromkeys(missing[name])) or None
            if reason and abstract_only:
                reason = ('当前所提供摘要范围内（abstract-only）：' if project.language.lower().startswith('zh')
                          else 'Abstract-only scope: ') + reason
            fields[name] = ExtractedField(missing_reason=reason)
        claims, evidence = [], []
        seen = set()
        for field, candidate, units in candidates:
            selected = [units[quote_id] for quote_id in candidate.quote_ids]
            key = (field, candidate.value, tuple((unit['passage_id'], unit['start'], unit['end']) for unit in selected))
            if key in seen:
                continue
            seen.add(key)
            scope = 'abstract' if any(unit['source_scope'] == 'abstract' for unit in selected) else 'fulltext'
            claim = ClaimRevision(run_id=run_id, text=candidate.value, source_scope=scope, status='needs_recheck')
            items = []
            for unit in selected:
                passage = passages[unit['passage_id']]
                quote = passage.text[unit['start']:unit['end']]
                if quote != unit['text']:
                    raise ScholarError(409, 'invalid_reader_source', 'Reader source anchor no longer matches its frozen text')
                items.append(Evidence(claim_revision_id=claim.id, passage_id=passage.id, quote=quote,
                    relation=candidate.relation, locator_status='located',
                    quote_start=unit['start'], quote_end=unit['end']))
            claims.append(claim)
            evidence.extend(items)
            cell = fields[field]
            fields[field] = ExtractedField(value='\n'.join(v for v in [cell.value, candidate.value] if v),
                claim_ids=cell.claim_ids + [claim.id], evidence_ids=cell.evidence_ids + [item.id for item in items],
                review_status='needs_recheck')
        with self.db.transaction() as conn:
            previous = self.latest(project.id, paper_version_id, conn)
            extraction = ExtractionRevision(project_id=project.id, run_id=run_id, paper_version_id=paper_version_id,
                document_version_ids=[d.id for d in documents], fields=fields,
                revision=previous.revision + 1 if previous else 1, previous_id=previous.id if previous else None,
                coverage={'complete': True, 'reader_version': READER_VERSION,
                          'reader_output_audit_ids': audits, 'batch_job_ids': batch_jobs,
                          'fragments': [{k: v for k, v in f.items() if k != 'text'} for f in fragments]})
            for claim in claims:
                self.records.insert('claim', claim, project_id=project.id, links=[('run', run_id)], conn=conn)
            for item in evidence:
                self.records.insert('evidence', item, project_id=project.id,
                                    links=[('claim', item.claim_revision_id), ('passage', item.passage_id)], conn=conn)
            links = [('version', paper_version_id), ('run', run_id)] + [('document', d.id) for d in documents]
            links += [('claim', c.id) for c in claims] + [('evidence', e.id) for e in evidence]
            return self.records.insert('extraction', extraction, project_id=project.id, links=links, conn=conn)

    def edit(self, project_id, paper_id, changes, expected_revision, *, published_only=False, expected_paper_version_id=None):
        if set(changes) - set(FIELD_NAMES):
            raise ScholarError(422, 'unknown_field', 'Only comparison matrix fields can be edited')
        with self.db.transaction() as conn:
            association = PaperStore(self.db).association(project_id, paper_id, conn)
            if expected_paper_version_id and association.paper_version_id != expected_paper_version_id:
                raise ScholarError(409, 'paper_version_changed', 'The selected paper version changed; reload before editing its extraction')
            latest = self.latest(project_id, association.paper_version_id, conn)
            if published_only:
                from ..persistence.projections import Published
                previous = Published(self.db).extractions(project_id, conn).get(association.paper_version_id)
            else:
                previous = latest
            if previous is None:
                raise ScholarError(404, 'extraction_not_found', 'Read this paper before editing its extraction')
            if previous.revision != expected_revision:
                raise revision_conflict(previous.revision)
            fields = dict(previous.fields)
            new_claims = []
            for name, value in changes.items():
                if value is not None and not isinstance(value, str):
                    raise ScholarError(422, 'invalid_field_value', 'Matrix values must be text or null')
                old_ids = fields[name].claim_ids
                prior = self.records.get('claim', old_ids[0], ClaimRevision, conn) if old_ids else None
                claim = ClaimRevision(run_id=previous.run_id, text=value, previous_id=prior.id if prior else None,
                    source_scope=prior.source_scope if prior else 'abstract') if value else None
                if claim:
                    new_claims.append(claim)
                fields[name] = ExtractedField(value=value or None, claim_ids=[claim.id] if claim else [], review_status='needs_recheck')
            result = previous.model_copy(update={'id': str(uuid4()), 'created_at': utcnow(), 'revision': latest.revision + 1,
                'previous_id': previous.id, 'fields': fields, 'origin': 'human'})
            for claim in new_claims:
                self.records.insert('claim', claim, project_id=project_id,
                    links=[('run', claim.run_id)] + ([('previous', claim.previous_id)] if claim.previous_id else []), conn=conn)
            return self.records.insert('extraction', result, project_id=project_id,
                links=[('previous', previous.id), ('version', result.paper_version_id)] +
                      [('claim', cid) for field in fields.values() for cid in field.claim_ids], conn=conn)
