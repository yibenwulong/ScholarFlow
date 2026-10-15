from ..domain.evidence import DocumentVersion, ExtractionRevision
from ..domain.reports import ReportVersion, VerificationRecord
from .records import RecordStore
from .paper_store import PaperStore
from .evidence_store import EvidenceStore


class ReportStore:
    def __init__(self, db):
        self.db = db
        self.records = RecordStore(db)

    def get(self, version_id, conn=None):
        return self.records.get('report', version_id, ReportVersion, conn)

    def list(self, project_id, conn=None):
        return sorted(self.records.list('report', ReportVersion, project_id=project_id, conn=conn), key=lambda v: v.revision, reverse=True)

    def verifications(self, version_id, *, published_only=False):
        values = [v for v in self.records.list('verification', VerificationRecord) if v.report_version_id == version_id]
        if published_only:
            from .projections import Published
            ids = Published(self.db).verification_ids(self.get(version_id).project_id)
            values = [v for v in values if v.id in ids]
        return values

    def is_outdated(self, version_id):
        report = self.get(version_id)
        extractions = self.records.list('extraction', ExtractionRevision, project_id=report.project_id)
        associations = PaperStore(self.db).associations(report.project_id)
        for extraction_id in report.manifest.get('extraction_ids', []):
            extraction = self.records.get('extraction', extraction_id, ExtractionRevision)
            if any(e.origin == 'human' and e.paper_version_id == extraction.paper_version_id and
                   e.revision > extraction.revision for e in extractions):
                return True
            if not any(a.paper_version_id == extraction.paper_version_id for a in associations):
                return True
            for document_id in extraction.document_version_ids:
                document = self.records.get('document', document_id, DocumentVersion)
                if any(d.kind == document.kind and d.created_at > document.created_at
                       for d in EvidenceStore(self.db).documents(document.paper_version_id)):
                    return True
        return False
