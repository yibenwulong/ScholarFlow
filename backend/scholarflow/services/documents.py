import hashlib
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, uuid5

import pymupdf

from ..domain.errors import ScholarError, not_found
from ..domain.evidence import DocumentVersion, Passage
from ..domain.library import PaperVersion, SourceRecord
from ..persistence.evidence_store import EvidenceStore
from ..persistence.file_store import FileStore
from .library import LibraryService


class DocumentService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.store = EvidenceStore(db)
        self.files = FileStore(settings.data_dir)
        self.library = LibraryService(db, settings)

    def get(self, document_id):
        return self.store.document(document_id)

    def import_pdf(self, project_id, paper_version_id, data, original_name):
        version = self.store.records.get('paper_version', paper_version_id, PaperVersion)
        self.library.association(project_id, version.paper_id)
        if len(data) > self.settings.max_file_bytes:
            raise ScholarError(413, 'file_too_large', 'PDF exceeds the configured size limit')
        if not data.startswith(b'%PDF-'):
            raise ScholarError(422, 'not_pdf', 'The uploaded file is not a PDF')
        stored = self.files.put(data)
        with self.db.transaction() as conn:
            for existing in self.store.documents(paper_version_id, conn):
                if existing.kind == 'pdf' and existing.sha256 == stored.sha256:
                    return existing
            display_name = PurePosixPath(original_name.replace('\\', '/')).name
            display_name = ''.join(c for c in display_name if c >= ' ' and c != '\x7f')[:255]
            document = DocumentVersion(paper_version_id=paper_version_id, kind='pdf',
                                       sha256=stored.sha256, storage_key=stored.key, original_name=display_name)
            return self.store.records.insert('document', document, links=[('version', paper_version_id)], conn=conn)

    def import_abstract(self, paper_version_id, text, source_record_id):
        source = self.store.records.get('source_record', source_record_id, SourceRecord)
        if source.paper_version_id != paper_version_id:
            raise not_found('source for this paper version')
        if not text.strip():
            raise ScholarError(422, 'empty_abstract', 'The source has no usable abstract')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        with self.db.transaction() as conn:
            for existing in self.store.documents(paper_version_id, conn):
                if existing.kind == 'abstract' and existing.sha256 == digest:
                    return existing
            document = DocumentVersion(paper_version_id=paper_version_id, kind='abstract', sha256=digest,
                                       abstract=text, source_record_id=source_record_id)
            return self.store.records.insert('document', document,
                links=[('version', paper_version_id), ('source', source_record_id)], conn=conn)

    def parse(self, document_id):
        document = self.get(document_id)
        if document.parse_status == 'ready':
            return self.store.passages(document_id)
        passages, status, error = [], 'ready', None
        if document.kind == 'abstract':
            text = document.abstract or ''
            passages = [self._passage(document.id, text, None)] if text.strip() else []
        else:
            content = self.files.resolve(document.storage_key).read_bytes()
            if hashlib.sha256(content).hexdigest() != document.sha256:
                raise ScholarError(409, 'document_hash_mismatch', 'Stored PDF content no longer matches its version')
            try:
                with pymupdf.open(stream=content, filetype='pdf') as pdf:
                    if pdf.needs_pass:
                        status = 'encrypted'
                    elif pdf.page_count > self.settings.max_pdf_pages:
                        status = 'page_limit_exceeded'
                    else:
                        for number, pdf_page in enumerate(pdf, 1):
                            text = pdf_page.get_text()
                            if text.strip():
                                passages.append(self._passage(document.id, text, number))
                        if not passages:
                            status = 'no_text_layer'
            except pymupdf.FileDataError as exc:
                status, error = 'invalid_pdf', type(exc).__name__
            except (RuntimeError, ValueError) as exc:
                status, error = 'invalid_pdf', type(exc).__name__
        if status != 'ready':
            passages = []
        with self.db.transaction() as conn:
            current = self.store.document(document_id, conn)
            if current.parse_status == 'ready':
                return self.store.passages(document_id, conn)
            for passage in passages:
                self.store.records.insert('passage', passage, links=[('document', document_id)], conn=conn)
            self.store.records.replace('document', current.model_copy(update={'parse_status': status, 'parse_error': error}),
                                       expected_revision=current.revision, conn=conn)
        return passages

    @staticmethod
    def _passage(document_id, text, page_number):
        return Passage(id=str(uuid5(NAMESPACE_URL, f'scholarflow:{document_id}:{page_number}')),
                       document_version_id=document_id, text=text, page_number=page_number, start=0, end=len(text))
