from fastapi import APIRouter, Header
from fastapi.responses import FileResponse
from pydantic import Field

from ..domain.common import Model
from ..domain.errors import not_found
from ..domain.operations import ExportFormat
from ..domain.reports import ReportDraft
from ..domain.evidence import ClaimRevision, Evidence, ExtractionRevision
from ..domain.runs import RunCreate
from ..persistence.projections import Published
from ..services.citations import render_citations
from ..services.exports import FORMATS
from .projects import page
from .runs import operation_key


class ReportWrite(Model):
    expected_revision: int = Field(ge=0)
    draft: ReportDraft
    confirmed_binding_blocks: list[str] = Field(default_factory=list)


class ExportWrite(Model):
    format: ExportFormat
    verification_id: str
    draft_acceptance_id: str | None = None


def report_router(services):
    router = APIRouter()

    def published(version_id):
        version = services.reports.get(version_id)
        if version.id not in Published(services.db).report_ids(version.project_id):
            raise not_found('published report version')
        return version

    @router.get('/projects/{project_id}/reports')
    def reports(project_id: str):
        services.projects.get(project_id)
        ids = Published(services.db).report_ids(project_id)
        return page([v for v in services.reports.store.list(project_id) if v.id in ids])

    @router.post('/projects/{project_id}/reports', status_code=201)
    def save(project_id: str, data: ReportWrite):
        result = services.reports.save(project_id, data.draft, data.expected_revision, published_only=True,
                                       confirmed_binding_blocks=data.confirmed_binding_blocks)
        with services.db.transaction() as conn:
            conn.execute('INSERT INTO entity_links(owner_id,relation,target_id) VALUES (?,?,?)', (result.id, 'human_publication', result.run_id))
        return result

    @router.get('/reports/{version_id}')
    def report(version_id: str):
        version = published(version_id)
        records = services.reports.store.verifications(version.id, published_only=True)
        rendered = render_citations(version)
        return {'version': version, 'verification': records[0] if records else None, 'outdated': services.reports.store.is_outdated(version.id),
                'citations': version.occurrences, 'rendered_markdown': rendered.markdown, 'references': rendered.references}

    @router.post('/reports/{version_id}/checks', status_code=202)
    def check(version_id: str, key: str | None = Header(default=None, alias='Idempotency-Key')):
        version = published(version_id)
        return services.runs.create(version.project_id, RunCreate(kind='report_check', target_id=version.id), operation_key(key))

    @router.get('/reports/{version_id}/citation-options')
    def citation_options(version_id: str):
        version = published(version_id)
        keys = {b.paper_version_id: b for b in version.bindings}
        items, seen = [], set()
        for extraction_id in version.manifest.get('extraction_ids', []):
            extraction = services.reports.records.get('extraction', extraction_id, ExtractionRevision)
            binding = keys.get(extraction.paper_version_id)
            if not binding:
                continue
            for field in extraction.fields.values():
                for eid in field.evidence_ids:
                    if eid in seen:
                        continue
                    seen.add(eid)
                    evidence = services.reports.records.get('evidence', eid, Evidence)
                    claim = services.reports.records.get('claim', evidence.claim_revision_id, ClaimRevision)
                    items.append({'key': binding.key, 'title': binding.bibliography_snapshot['title'],
                        'claim_revision_id': claim.id, 'evidence_id': eid, 'quote': evidence.quote,
                        'source_scope': claim.source_scope, 'locator_status': evidence.locator_status})
        return page(items)

    @router.post('/reports/{version_id}/exports', status_code=202)
    def export(version_id: str, data: ExportWrite, key: str | None = Header(default=None, alias='Idempotency-Key')):
        version = published(version_id)
        if data.verification_id not in Published(services.db).verification_ids(version.project_id):
            raise not_found('published verification record')
        job = services.exports.request(version.id, data.format, data.verification_id, data.draft_acceptance_id, operation_key(key), published_only=True)
        run = services.runs.create(version.project_id, RunCreate(kind='export', target_id=job.id), 'export:' + job.id)
        return {'export': job, 'run_id': run.id}

    @router.get('/reports/{version_id}/exports')
    def exports(version_id: str):
        published(version_id)
        return page(services.exports.list(version_id))

    @router.get('/exports/{export_id}/file')
    def file(export_id: str):
        job = services.exports.get(export_id)
        if job.status != 'ready':
            raise not_found('completed export')
        return FileResponse(services.exports.resolve(job.storage_key, job.format), media_type=FORMATS[job.format][1],
                            filename=f'ScholarFlow-{job.report_version_id}.{FORMATS[job.format][0]}')

    return router
