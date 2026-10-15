"""Expose only fenced workflow publications and explicit human saves."""
from ..domain.evidence import ExtractionRevision
from ..domain.runs import Run
from .records import RecordStore


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
