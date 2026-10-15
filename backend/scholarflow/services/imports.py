import re

from ..domain.errors import ScholarError
from ..domain.library import PaperCandidate, PaperVersion
from ..domain.operations import ImportJob
from ..persistence.records import RecordStore
from ..persistence.run_store import fingerprint
from .documents import DocumentService
from .library import LibraryService
from .providers.base import normalize_identifiers


class ImportService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.records = RecordStore(db)
        self.documents = DocumentService(db, settings)
        self.library = LibraryService(db, settings)

    def get(self, job_id, conn=None):
        return self.records.get('import', job_id, ImportJob, conn)

    def request(self, project_id, data, operation_key, *, content=None, filename='', paper_version_id=None):
        self.library.projects.get(project_id)
        if paper_version_id:
            version = self.records.get('paper_version', paper_version_id, PaperVersion)
            self.library.association(project_id, version.paper_id)
        upload_key = None
        if content is not None:
            if len(content) > self.settings.max_file_bytes:
                raise ScholarError(413, 'file_too_large', 'PDF exceeds the configured limit')
            if not content.startswith(b'%PDF-'):
                raise ScholarError(422, 'not_pdf', 'Upload a valid PDF')
            upload_key = self.documents.files.put(content).key
        identifiers = normalize_identifiers(data.identifiers)
        if not content and not data.candidates and not identifiers:
            raise ScholarError(422, 'empty_import', 'Supply a PDF, academic identifiers or selected metadata records')
        values = dict(data.model_dump(), identifiers=identifiers, upload_key=upload_key,
                      original_name=filename, paper_version_id=paper_version_id)
        scope, digest = f'import:{project_id}', fingerprint(values)
        with self.db.transaction() as conn:
            prior = conn.execute('SELECT * FROM idempotency WHERE scope=? AND operation_key=?', (scope, operation_key)).fetchone()
            if prior:
                if prior['request_hash'] != digest:
                    raise ScholarError(409, 'idempotency_conflict', 'Import key has different content')
                return self.get(prior['entity_id'], conn)
            job = ImportJob(project_id=project_id, **values)
            self.records.insert('import', job, project_id=project_id, links=[('project', project_id)], conn=conn)
            conn.execute('INSERT INTO idempotency(scope,operation_key,request_hash,entity_id) VALUES (?,?,?,?)', (scope, operation_key, digest, job.id))
            return job

    async def build(self, job_id):
        job = self.get(job_id)
        candidates = list(job.candidates)
        if job.identifiers:
            from .providers.arxiv import ArxivProvider
            from .providers.semantic_scholar import SemanticScholarProvider
            if 'arxiv' in job.identifiers:
                result = await ArxivProvider().search('id:' + job.identifiers['arxiv'], limit=5)
            elif 'doi' in job.identifiers:
                result = await SemanticScholarProvider().search(job.identifiers['doi'], limit=5)
            else:
                raise ScholarError(422, 'unsupported_identifier', 'Import supports exact DOI or arXiv identifiers')
            if result.error:
                raise ScholarError(502, 'identifier_lookup_failed', 'Academic metadata lookup failed', retryable=True)
            def matches_id(candidate, key, value):
                actual = candidate.identifiers.get(key, '')
                if key == 'arxiv' and not re.search(r'v\d+$', value):
                    return re.sub(r'v\d+$', '', actual) == value
                return actual == value
            matches = [c for c in result.candidates if all(matches_id(c, k, v) for k, v in job.identifiers.items())]
            if not matches:
                raise ScholarError(422, 'identifier_not_found', 'The source returned no exact matching identifier')
            candidates.extend(matches)
        if job.upload_key and not job.paper_version_id and not candidates:
            candidates.append(PaperCandidate(title=job.original_name.rsplit('.', 1)[0] or 'Uploaded paper',
                                             source='local', source_id='upload:' + job.upload_key))
        papers = self.library.import_candidates(job.project_id, candidates) if candidates else []
        version_ids = [p.paper_version_id for p in papers]
        if job.paper_version_id:
            version_ids.append(job.paper_version_id)
        documents = []
        if job.upload_key:
            if len(set(version_ids)) != 1:
                raise ScholarError(422, 'ambiguous_pdf_version', 'A PDF import must target exactly one paper version')
            content = self.documents.files.resolve(job.upload_key).read_bytes()
            document = self.documents.import_pdf(job.project_id, version_ids[0], content, job.original_name)
            self.documents.parse(document.id)
            documents.append(document.id)
        return {'paper_version_ids': list(dict.fromkeys(version_ids)), 'document_version_ids': documents}
