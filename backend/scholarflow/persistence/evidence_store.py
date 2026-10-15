from ..domain.evidence import DocumentVersion, Passage
from .records import RecordStore


class EvidenceStore:
    def __init__(self, db):
        self.db = db
        self.records = RecordStore(db)

    def document(self, document_id, conn=None):
        return self.records.get('document', document_id, DocumentVersion, conn)

    def documents(self, paper_version_id, conn=None):
        return self._related('document', DocumentVersion, 'version', paper_version_id, conn)

    def passages(self, document_id, conn=None):
        rows = self._related('passage', Passage, 'document', document_id, conn)
        return sorted(rows, key=lambda p: (p.page_number or 0, p.start, p.id))

    def _related(self, kind, model, relation, target_id, conn=None):
        from contextlib import nullcontext
        with (nullcontext(conn) if conn is not None else self.db.transaction(write=False)) as cx:
            rows = cx.execute('SELECT e.payload FROM entities e JOIN entity_links l ON l.owner_id=e.id '
                              'WHERE e.kind=? AND l.relation=? AND l.target_id=? ORDER BY e.created_at DESC,e.id',
                              (kind, relation, target_id))
            return [model.model_validate_json(row['payload']) for row in rows]
