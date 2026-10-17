"""Expose only fenced workflow publications and explicit human saves."""
from contextlib import nullcontext

from pydantic import ValidationError

from ..domain.errors import ScholarError
from ..domain.evidence import DocumentVersion, ExtractionRevision
from ..domain.runs import Run
from .records import RecordStore
from .run_store import parse_job


def reading_extraction(records, project_id, job, conn=None):
    """A successful fenced job, not a bare entity, authorizes publication."""
    if job.kind != 'artifact' or job.stage != 'read_paper' or job.status != 'succeeded':
        return None
    manifest = job.input_manifest
    if manifest.get('_project_id') != project_id or not isinstance(job.result, dict):
        return None
    document_ids = manifest.get('document_ids')
    if not isinstance(document_ids, list) or not document_ids or not all(isinstance(value, str) for value in document_ids):
        return None
    try:
        run = records.get('run', job.run_id, Run, conn)
        extraction = records.get('extraction', job.result.get('extraction_id'), ExtractionRevision, conn)
        if (run.project_id != project_id or extraction.project_id != project_id
                or extraction.paper_version_id != manifest.get('paper_version_id')
                or extraction.document_version_ids != list(dict.fromkeys(document_ids))):
            return None
        for document_id in document_ids:
            document = records.get('document', document_id, DocumentVersion, conn)
            if document.paper_version_id != extraction.paper_version_id:
                return None
        return extraction
    except (ScholarError, ValidationError):
        return None


class Published:
    def __init__(self, db):
        self.db = db
        self.records = RecordStore(db)

    def report_ids(self, project_id):
        runs = self.records.list('run', Run, project_id=project_id)
        ids = {value['report_version_id'] for run in runs for value in run.manifests.values()
               if isinstance(value, dict) and 'report_version_id' in value}
        with self.db.transaction(write=False) as conn:
            ids.update(row[0] for row in conn.execute("SELECT l.owner_id FROM entity_links l JOIN entities e ON e.id=l.owner_id WHERE l.relation='human_publication' AND e.project_id=?", (project_id,)))
        return ids

    def verification_ids(self, project_id):
        from ..domain.reports import VerificationRecord
        runs = self.records.list('run', Run, project_id=project_id)
        ids = {value['verification_record_id'] for run in runs for value in run.manifests.values()
               if isinstance(value, dict) and 'verification_record_id' in value}
        with self.db.transaction(write=False) as conn:
            human_reports = {row[0] for row in conn.execute("SELECT owner_id FROM entity_links WHERE relation='human_publication'")}
        ids.update(v.id for v in self.records.list('verification', VerificationRecord, project_id=project_id)
                   if v.checker == 'edit_projection_v1' and v.report_version_id in human_reports)
        return ids

    def extractions(self, project_id, conn=None):
        runs = self.records.list('run', Run, project_id=project_id, conn=conn)
        ids = {eid for run in runs for eid in run.manifests.get('extractions', {}).get('extraction_ids', [])}
        all_items = self.records.list('extraction', ExtractionRevision, project_id=project_id, conn=conn)
        with (nullcontext(conn) if conn is not None else self.db.transaction(write=False)) as cx:
            rows = cx.execute("SELECT j.* FROM jobs j JOIN entities r ON r.id=j.run_id "
                "WHERE r.project_id=? AND j.kind='artifact' AND j.stage='read_paper' AND j.status='succeeded'",
                (project_id,)).fetchall()
            for row in rows:
                extraction = reading_extraction(self.records, project_id, parse_job(row), cx)
                if extraction:
                    ids.add(extraction.id)
        changed = True
        while changed:
            before = len(ids)
            ids.update(e.id for e in all_items if e.origin == 'human' and e.previous_id in ids)
            changed = len(ids) != before
        by_version = {}
        for item in all_items:
            if item.id in ids and (item.paper_version_id not in by_version or by_version[item.paper_version_id].revision < item.revision):
                by_version[item.paper_version_id] = item
        return by_version
