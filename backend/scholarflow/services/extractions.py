import json
from uuid import uuid4
from typing import Literal

from pydantic import Field, ValidationError

from ..domain.common import Model, utcnow
from ..domain.errors import ScholarError, revision_conflict
from ..domain.evidence import ClaimRevision, Evidence, ExtractionRevision, ExtractedField
from ..domain.library import PaperVersion
from ..domain.projects import Project
from ..persistence.evidence_store import EvidenceStore
from ..persistence.paper_store import PaperStore
from ..persistence.run_store import RunStore
from ..workflows.prompts import FIELD_NAMES, READ_PROMPT
from .model_gateway import ModelRequest
from .verification import locate_quote


class Candidate(Model):
    value: str = Field(min_length=1)
    passage_id: str
    quote: str = Field(min_length=1)
    relation: Literal['supports', 'refutes', 'insufficient'] = 'supports'


class ReaderOutput(Model):
    fields: dict[str, list[Candidate]]


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

    def get(self, extraction_id):
        return self.records.get('extraction', extraction_id, ExtractionRevision)

    def latest(self, project_id, paper_version_id, conn=None):
        items = [e for e in self.records.list('extraction', ExtractionRevision, project_id=project_id, conn=conn)
                 if e.paper_version_id == paper_version_id]
        return max(items, key=lambda e: (e.revision, e.created_at, e.id)) if items else None

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
        candidates = []
        for batch in batches:
            response = await self.gateway.complete(ModelRequest(run_id=run_id, purpose='read_paper', messages=[
                {'role': 'system', 'content': READ_PROMPT}, {'role': 'user', 'content': json.dumps({
                    'question': project.question, 'language': project.language, 'passages': batch}, ensure_ascii=False)}]))
            try:
                output = ReaderOutput.model_validate_json(response.text)
                if set(output.fields) - set(FIELD_NAMES):
                    raise ValueError('Unknown extraction field')
            except (ValueError, ValidationError):
                raise ScholarError(422, 'invalid_model_output', 'Reader returned invalid structured fields; review or retry this paper') from None
            candidates.extend((field, candidate, batch) for field, values in output.fields.items() for candidate in values)
        fields = {name: ExtractedField() for name in FIELD_NAMES}
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
            location = locate_quote(passage, candidate.quote)
            in_batch = location.located and any(f['passage_id'] == passage.id and
                f['start'] <= location.start and location.end <= f['end'] for f in batch)
            scope = 'abstract' if kinds[passage.document_version_id] == 'abstract' else 'fulltext'
            status = 'needs_recheck' if in_batch else 'insufficient_evidence'
            claim = ClaimRevision(run_id=run_id, text=candidate.value, source_scope=scope, status=status)
            item = Evidence(claim_revision_id=claim.id, passage_id=passage.id, quote=candidate.quote,
                relation=candidate.relation, locator_status='located' if in_batch else 'unlocated',
                quote_start=location.start if in_batch else None, quote_end=location.end if in_batch else None)
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
                coverage={'complete': True, 'fragments': [{k: v for k, v in f.items() if k != 'text'} for f in fragments]})
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
