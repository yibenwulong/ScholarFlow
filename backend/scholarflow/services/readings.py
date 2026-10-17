"""Project-scoped Reader outcomes and isolated retries of frozen failed jobs."""
from copy import deepcopy

from pydantic import ValidationError

from ..domain.errors import ScholarError, not_found
from ..domain.evidence import DocumentVersion
from ..domain.library import PaperVersion, ProjectPaper
from ..domain.projects import Project
from ..domain.runs import Run
from ..persistence.projections import Published, reading_extraction
from ..persistence.run_store import RunStore, fingerprint, parse_job
from .model_gateway import ModelConfig
from .reader_contract import READER_VERSION, reader_inputs


def needs_reread(extraction):
    return (extraction is not None and extraction.origin == 'ai'
            and extraction.coverage.get('reader_version') != READER_VERSION)


class ReadingService:
    def __init__(self, db):
        self.db = db
        self.store = RunStore(db)
        self.records = self.store.records

    def _original_jobs(self, project_id, conn):
        rows = conn.execute("SELECT j.* FROM jobs j JOIN entities r ON r.id=j.run_id "
            "WHERE r.project_id=? AND j.kind='artifact' AND j.stage='read_paper' "
            'ORDER BY r.created_at DESC,j.created_at DESC,j.id DESC', (project_id,)).fetchall()
        jobs = []
        for row in rows:
            job = parse_job(row)
            if self.store.get_run(job.run_id, conn).kind == 'literature_review':
                jobs.append(job)
        return jobs

    def _retries(self, project_id, source_job_id, conn):
        return [run for run in self.records.list('run', Run, project_id=project_id, conn=conn)
                if run.kind == 'paper_read' and run.target_id == source_job_id]

    def _frozen_inputs(self, project_id, job, conn):
        source = self.store.get_run(job.run_id, conn)
        if source.project_id != project_id:
            raise not_found('reading task')
        if source.kind != 'literature_review' or job.kind != 'artifact' or job.stage != 'read_paper':
            raise ScholarError(409, 'not_reader_task', 'Only an original Reader task can be retried')
        inputs = job.input_manifest
        try:
            snapshot = inputs['snapshot']
            project = Project.model_validate(snapshot['project'])
            ModelConfig.model_validate(snapshot['model'])
            documents = inputs['document_ids']
            limit = snapshot['limits']['max_model_calls']
            valid = (inputs.get('_project_id') == project_id and project.id == project_id
                     and isinstance(documents, list) and bool(documents)
                     and all(isinstance(value, str) and value for value in documents)
                     and isinstance(inputs['paper_version_id'], str)
                     and type(limit) is int and limit > 0
                     and fingerprint(inputs) == job.input_hash)
        except (KeyError, TypeError, ValidationError):
            valid = False
        if not valid:
            raise ScholarError(409, 'invalid_reading_inputs', 'The original Reader inputs are incomplete')
        return source, deepcopy(inputs)

    def _block_reason(self, project_id, job, conn, *, allow_saved=False):
        if job.status not in {'failed', 'succeeded'}:
            return 'Only a failed Reader task can be retried'
        try:
            _, inputs = self._frozen_inputs(project_id, job, conn)
            paper = self.records.get('paper_version', inputs['paper_version_id'], PaperVersion, conn)
            associations = self.records.list('project_paper', ProjectPaper, project_id=project_id, conn=conn)
            current = next((item for item in associations if item.paper_id == paper.paper_id), None)
            if current is None or current.paper_version_id != paper.id:
                return 'The selected paper version has changed'
            for document_id in inputs['document_ids']:
                document = self.records.get('document', document_id, DocumentVersion, conn)
                if document.paper_version_id != paper.id or document.parse_status != 'ready':
                    return 'An original document is no longer available for reading'
            visible = Published(self.db).extractions(project_id, conn).get(paper.id)
            if visible is not None and visible.origin == 'human':
                return 'A human extraction revision must be preserved'
            if job.status == 'succeeded' and not needs_reread(visible) and not allow_saved:
                return 'This paper has already been read successfully'
        except (ScholarError, ValidationError):
            return 'The original Reader inputs are no longer available'
        return None

    def status(self, project_id):
        with self.db.transaction(write=False) as conn:
            self.records.get('project', project_id, Project, conn)
            published = Published(self.db).extractions(project_id, conn)
            latest, items = set(), []
            for job in self._original_jobs(project_id, conn):
                paper_id = job.input_manifest.get('paper_version_id')
                if not isinstance(paper_id, str) or paper_id in latest:
                    continue
                latest.add(paper_id)
                retries = self._retries(project_id, job.id, conn)
                child = retries[0] if retries else None
                active = next((run for run in retries if run.status in {'queued', 'running'}), None)
                extraction = reading_extraction(self.records, project_id, job, conn)
                state, error = job.status, job.error
                if child is not None:
                    state = {'completed': 'succeeded', 'needs_review': 'failed',
                             'waiting_for_user': 'running'}.get(child.status, child.status)
                    error = child.issues[0] if child.issues else None
                    if state == 'succeeded':
                        extraction_ids = child.manifests.get('extractions', {}).get('extraction_ids', [])
                        child_jobs = conn.execute("SELECT * FROM jobs WHERE run_id=? AND kind='artifact' "
                                                 "AND stage='read_paper' AND status='succeeded'", (child.id,)).fetchall()
                        extraction = next((result for row in child_jobs
                            if (result := reading_extraction(self.records, project_id, parse_job(row), conn))
                            and result.id in extraction_ids), None)
                reason = self._block_reason(project_id, job, conn)
                incomplete = needs_reread(published.get(paper_id))
                if active:
                    reason = 'A retry is already queued or running'
                elif state == 'succeeded' and not incomplete:
                    reason = 'This paper has already been read successfully'
                elif child is not None and child.status != 'failed' and not (child.status == 'completed' and incomplete):
                    reason = 'The latest retry has not failed'
                items.append({'paper_version_id': paper_id, 'source_run_id': job.run_id, 'source_job_id': job.id,
                    'status': state, 'extraction_id': extraction.id if extraction else None,
                    'error': {'code': error.get('code', 'operation_failed'), 'message': error.get('message', 'Reader failed')}
                             if isinstance(error, dict) else None,
                    'can_retry': reason is None, 'active_retry_run_id': active.id if active else None,
                    'needs_reread': incomplete,
                    'retry_block_reason': reason})
            return {'items': items}

    def validate_retry(self, run, generation):
        """Recheck mutable ownership/manual work before and after remote work."""
        with self.db.transaction(write=False) as conn:
            current = self.store.get_run(run.id, conn)
            if current.execution_generation != generation or current.status == 'cancelled':
                raise ScholarError(409, 'lease_lost', 'This Reader retry was cancelled or replaced')
            job = self.store.get_job(run.target_id, conn)
            _, inputs = self._frozen_inputs(run.project_id, job, conn)
            reading = run.manifests.get('reading', {})
            if (reading.get('source_job_id') != job.id or reading.get('input_hash') != job.input_hash
                    or reading.get('input_manifest') != inputs or run.request_snapshot != inputs['snapshot']):
                raise ScholarError(409, 'invalid_reading_inputs', 'The frozen Reader inputs do not match their source')
            effective = reader_inputs(inputs)
            saved = False
            for row in conn.execute("SELECT * FROM jobs WHERE run_id=? AND kind='artifact' "
                                    "AND stage='read_paper' AND status='succeeded'", (run.id,)):
                own_job = parse_job(row)
                extraction = reading_extraction(self.records, run.project_id, own_job, conn)
                if (own_job.input_hash == fingerprint(effective) and extraction is not None
                        and extraction.coverage.get('reader_version') == READER_VERSION):
                    saved = True
                    break
            reason = self._block_reason(run.project_id, job, conn, allow_saved=saved)
            if reason:
                raise ScholarError(409, 'reading_retry_blocked', reason)
            if reading.get('reader_version', READER_VERSION) != READER_VERSION:
                raise ScholarError(409, 'reader_version_changed', 'Create a new reading with the current extraction version')
            return effective

    def retry(self, project_id, source_job_id, operation_key):
        scope, digest = f'paper_read_retry:{project_id}', fingerprint({'source_job_id': source_job_id})
        with self.db.transaction() as conn:
            self.records.get('project', project_id, Project, conn)
            previous = conn.execute('SELECT * FROM idempotency WHERE scope=? AND operation_key=?',
                                    (scope, operation_key)).fetchone()
            if previous:
                if previous['request_hash'] != digest:
                    raise ScholarError(409, 'idempotency_conflict', 'This key was used for another paper retry')
                return self.store.get_run(previous['entity_id'], conn)
            if not isinstance(source_job_id, str) or not source_job_id:
                raise ScholarError(422, 'target_required', 'Choose a failed Reader task')
            job = self.store.get_job(source_job_id, conn)
            source, inputs = self._frozen_inputs(project_id, job, conn)
            reason = self._block_reason(project_id, job, conn)
            if reason:
                raise ScholarError(409, 'reading_retry_blocked', reason)
            retries = self._retries(project_id, source_job_id, conn)
            reusable = next((run for run in retries if run.status in {'queued', 'running'} or (
                run.status == 'completed' and run.manifests.get('reading', {}).get('reader_version') == READER_VERSION)), None)
            if reusable is not None:
                run = reusable
            else:
                if retries and retries[0].status != 'failed' and not (
                    retries[0].status == 'completed' and retries[0].manifests.get('reading', {}).get('reader_version') != READER_VERSION):
                    raise ScholarError(409, 'reading_retry_blocked', 'The latest paper retry has not failed')
                run = Run(project_id=project_id, kind='paper_read', target_id=source_job_id,
                    start_at='analyze', stage='read_paper', request_snapshot=deepcopy(inputs['snapshot']),
                    manifests={'reading': {'source_run_id': source.id, 'source_job_id': job.id,
                        'input_hash': job.input_hash, 'input_manifest': inputs, 'reader_version': READER_VERSION}})
                self.records.insert('run', run, project_id=project_id,
                                    links=[('project', project_id), ('source_run', source.id)], conn=conn)
                self.store.enqueue(run.id, 'driver', 'paper_read', {'operation_revision': 0}, operation_key, conn)
                self.store.append_event(run.id, 'queued', 'read_paper', {'source_job_id': job.id}, conn)
            conn.execute('INSERT INTO idempotency(scope,operation_key,request_hash,entity_id) VALUES (?,?,?,?)',
                         (scope, operation_key, digest, run.id))
            return run
