import hashlib
import json
import re
from uuid import uuid4
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

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
from .model_gateway import ModelRequest
from .reader_contract import READER_VERSION, reader_inputs
from .verification import locate_quote


class Candidate(Model):
    value: str = Field(min_length=1)
    passage_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    relation: Literal['supports', 'refutes', 'insufficient'] = 'supports'

    @field_validator('value', 'quote')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('A claim and its quote must contain text')
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
    known = set(FIELD_NAMES) | {'fields', 'missing_reasons', 'value', 'passage_id', 'quote', 'relation'}
    return [{'loc': [part if isinstance(part, int) or part in known else '<extra_field>' for part in item['loc']],
             'type': item['type']} for item in error.errors(include_input=False, include_url=False, include_context=False)]


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
    def _output(raw, batch):
        text, _ = reader_json_text(raw)
        output = ReaderOutput.model_validate_json(text)
        allowed = {fragment['passage_id'] for fragment in batch}
        for name in FIELD_NAMES:
            for index, candidate in enumerate(getattr(output.fields, name)):
                if candidate.passage_id not in allowed:
                    raise ScholarError(422, 'unknown_passage', 'Reader referenced a passage outside its supplied batch',
                        details={'diagnostics': [{'loc': ['fields', name, index, 'passage_id'], 'type': 'unknown_passage'}]})
        return output

    def _batch_result(self, job, manifest, batch):
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
            return self._output(audit.raw_text, batch), audit.id, job.id
        except (ScholarError, ValidationError, ValueError, TypeError):
            raise ScholarError(409, 'invalid_reader_batch_cache', 'The saved Reader batch does not match its frozen input') from None

    async def _read_batch(self, run, project, paper_version_id, documents, batch_index, batch):
        manifest = reader_inputs({'project': project.model_dump(mode='json'), 'paper_version_id': paper_version_id,
            'document_ids': [document.id for document in documents],
            'document_hashes': {document.id: document.sha256 for document in documents},
            'model': run.request_snapshot.get('model', {}),
            'prompt_sha256': hashlib.sha256(READ_PROMPT.encode('utf-8')).hexdigest(), 'batch_index': batch_index,
            'fragments': [{**{key: value for key, value in fragment.items() if key != 'text'},
                           'sha256': hashlib.sha256(fragment['text'].encode('utf-8')).hexdigest()} for fragment in batch],
            '_project_id': project.id})
        digest = fingerprint(manifest)
        cached = self.runs.cached_artifact('read_paper_batch', digest, project.id)
        if cached is not None:
            self._batch_result(cached, manifest, batch)

        async def operation():
            response = await self.gateway.complete(ModelRequest(run_id=run.id, purpose='read_paper', messages=[
                {'role': 'system', 'content': READ_PROMPT}, {'role': 'user', 'content': json.dumps({
                    'question': project.question, 'language': project.language, 'passages': batch}, ensure_ascii=False)}]))
            _, output_format = reader_json_text(response.text)
            failure, diagnostics = None, []
            try:
                self._output(response.text, batch)
            except ValidationError as error:
                diagnostics = reader_diagnostics(error)
                failure = ('invalid_model_output', 'Reader returned invalid structured fields; review this paper')
            except ScholarError as error:
                diagnostics = error.details['diagnostics']
                failure = (error.code, error.message)
            audit = ReaderOutputAudit(project_id=project.id, run_id=run.id, paper_version_id=paper_version_id,
                document_version_ids=manifest['document_ids'], input_hash=digest, raw_text=response.text,
                sha256=hashlib.sha256(response.text.encode('utf-8')).hexdigest(),
                provider_request_id=response.provider_request_id, usage=response.usage,
                output_format=output_format, diagnostics=diagnostics)
            self.records.insert('reader_output_audit', audit, project_id=project.id,
                links=[('run', run.id), ('version', paper_version_id)] + [('document', document.id) for document in documents])
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
        return self._batch_result(job, manifest, batch)

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
            output, audit_id, job_id = await self._read_batch(run, project, paper_version_id, documents, batch_index, batch)
            audits.append(audit_id)
            batch_jobs.append(job_id)
            for name in FIELD_NAMES:
                values = getattr(output.fields, name)
                candidates.extend((name, candidate, batch) for candidate in values)
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
        for field, candidate, batch in candidates:
            key = (field, candidate.value, candidate.passage_id, candidate.quote)
            if key in seen:
                continue
            seen.add(key)
            passage = passages.get(candidate.passage_id)
            if passage is None:
                # Unknown IDs must never be attached to another stored source.
                raise ScholarError(422, 'unknown_passage', 'Reader referenced a passage outside its input')
            located = next(((fragment, location) for fragment in batch if fragment['passage_id'] == passage.id
                and (location := locate_quote(passage.model_copy(update={'text': fragment['text']}), candidate.quote)).located), None)
            start = located[0]['start'] + located[1].start if located else None
            end = located[0]['start'] + located[1].end if located else None
            in_batch = located is not None
            scope = 'abstract' if kinds[passage.document_version_id] == 'abstract' else 'fulltext'
            status = 'needs_recheck' if in_batch else 'insufficient_evidence'
            claim = ClaimRevision(run_id=run_id, text=candidate.value, source_scope=scope, status=status)
            item = Evidence(claim_revision_id=claim.id, passage_id=passage.id, quote=candidate.quote,
                relation=candidate.relation, locator_status='located' if in_batch else 'unlocated',
                quote_start=start, quote_end=end)
            claims.append(claim)
            evidence.append(item)
            cell = fields[field]
            fields[field] = ExtractedField(value='\n'.join(v for v in [cell.value, candidate.value] if v),
                claim_ids=cell.claim_ids + [claim.id], evidence_ids=cell.evidence_ids + [item.id],
                review_status='insufficient_evidence' if status == 'insufficient_evidence' or cell.review_status == 'insufficient_evidence' else 'needs_recheck')
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
