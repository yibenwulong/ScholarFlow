from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse
from pydantic import Field

from ..domain.common import Model
from ..domain.errors import ScholarError
from ..domain.operations import ImportWrite
from ..domain.runs import RunCreate
from ..domain.projects import SelectionSnapshot
from ..domain.library import SourceRecord
from .runs import operation_key
from .projects import page


class PaperDecision(Model):
    expected_revision: int = Field(ge=1)
    decision: Literal['pending', 'included', 'excluded']
    reason: str = ''
    notes: str | None = None
    paper_version_id: str | None = None


class SelectionWrite(Model):
    expected_revision: int = Field(ge=0)
    paper_version_ids: list[str]


def library_router(services):
    router = APIRouter()

    @router.get('/projects/{project_id}/papers')
    def papers(project_id: str):
        return page(services.library.list_project(project_id))

    @router.post('/projects/{project_id}/imports', status_code=202)
    async def import_papers(project_id: str, request: Request, key: str | None = Header(default=None, alias='Idempotency-Key')):
        content, filename, version_id = None, '', None
        if request.headers.get('content-type', '').startswith('multipart/form-data'):
            async with request.form(max_files=1, max_fields=5) as form:
                upload = form.get('file')
                if upload is None or not hasattr(upload, 'read'):
                    raise ScholarError(422, 'pdf_required', 'Select a PDF file')
                chunks, size = [], 0
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > services.settings.max_file_bytes:
                        raise ScholarError(413, 'file_too_large', 'PDF exceeds the configured limit')
                    chunks.append(chunk)
                content, filename = b''.join(chunks), upload.filename or 'paper.pdf'
                version_id = form.get('paper_version_id') or None
                data = ImportWrite.model_validate_json(form.get('metadata', '{}'))
        else:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise ScholarError(413, 'metadata_too_large', 'Import metadata exceeds 2 MiB')
                chunks.append(chunk)
            data = ImportWrite.model_validate_json(b''.join(chunks))
        job = services.imports.request(project_id, data, operation_key(key), content=content, filename=filename, paper_version_id=version_id)
        run = services.runs.create(project_id, RunCreate(kind='import', target_id=job.id), 'import:' + job.id)
        return {'import_id': job.id, 'run_id': run.id, 'status': job.status}

    @router.get('/imports/{import_id}')
    def import_status(import_id: str):
        return services.imports.get(import_id)

    @router.patch('/projects/{project_id}/papers/{paper_id}')
    def decide(project_id: str, paper_id: str, data: PaperDecision):
        return services.library.decide(project_id, paper_id, data.decision, data.reason, data.expected_revision,
                                       notes=data.notes, paper_version_id=data.paper_version_id)

    @router.post('/projects/{project_id}/selections', status_code=201)
    def select(project_id: str, data: SelectionWrite):
        return services.library.freeze_selection(project_id, data.paper_version_ids, data.expected_revision)

    @router.get('/projects/{project_id}/selections')
    def selections(project_id: str):
        services.projects.get(project_id)
        return page(services.library.store.records.list('selection', SelectionSnapshot, project_id=project_id))

    @router.get('/papers/{paper_id}/versions')
    def versions(paper_id: str):
        sources = services.library.store.records.list('source_record', SourceRecord)
        return page([dict(v.model_dump(mode='json'), source_records=[r.model_dump(mode='json') for r in sources if r.paper_version_id == v.id])
                     for v in services.library.versions(paper_id)])

    @router.get('/documents/{document_id}')
    def document(document_id: str):
        return services.documents.get(document_id)

    @router.get('/documents/{document_id}/file')
    def document_file(document_id: str):
        document = services.documents.get(document_id)
        if document.kind != 'pdf' or not document.storage_key:
            from ..domain.errors import not_found
            raise not_found('PDF file')
        path = services.documents.files.resolve(document.storage_key)
        return FileResponse(path, media_type='application/pdf', filename=document.original_name or 'paper.pdf',
                            content_disposition_type='inline')

    @router.get('/documents/{document_id}/passages')
    def passages(document_id: str):
        services.documents.get(document_id)
        return page(services.documents.store.passages(document_id), limit=1000)

    return router
