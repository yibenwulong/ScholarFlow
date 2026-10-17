from fastapi import APIRouter, Header
from pydantic import Field

from ..domain.common import Model
from ..domain.errors import not_found, revision_conflict
from ..domain.evidence import ClaimRevision, Evidence, Passage
from ..domain.operations import EvidenceReview
from ..persistence.projections import Published
from ..services.readings import ReadingService
from .projects import page
from .runs import operation_key


class ExtractionEdit(Model):
    expected_revision: int = Field(ge=1)
    paper_version_id: str
    changes: dict[str, str | None]


class ReviewWrite(Model):
    expected_revision: int = Field(ge=0)
    verdict: str
    reason: str = Field(min_length=1)


class ReadingRetry(Model):
    source_job_id: str = Field(min_length=1)


def evidence_router(services):
    router = APIRouter()
    records = services.documents.store.records

    @router.get('/projects/{project_id}/reading-status')
    def reading_status(project_id: str):
        return ReadingService(services.db).status(project_id)

    @router.post('/projects/{project_id}/reading-retries', status_code=202)
    def reading_retry(project_id: str, data: ReadingRetry,
                      key: str | None = Header(default=None, alias='Idempotency-Key')):
        return {'run': ReadingService(services.db).retry(project_id, data.source_job_id, operation_key(key))}

    @router.get('/projects/{project_id}/extractions')
    def matrix(project_id: str):
        services.projects.get(project_id)
        return page(list(Published(services.db).extractions(project_id).values()))

    @router.get('/projects/{project_id}/extractions/{paper_id}')
    def extraction(project_id: str, paper_id: str):
        association = services.library.association(project_id, paper_id)
        result = Published(services.db).extractions(project_id).get(association.paper_version_id)
        if result is None:
            raise not_found('published extraction')
        return result

    @router.patch('/projects/{project_id}/extractions/{paper_id}')
    def edit(project_id: str, paper_id: str, data: ExtractionEdit):
        return services.extractions.edit(project_id, paper_id, data.changes, data.expected_revision, published_only=True,
                                         expected_paper_version_id=data.paper_version_id)

    @router.get('/claims/{claim_id}/evidence')
    def evidence(claim_id: str):
        claim = records.get('claim', claim_id, ClaimRevision)
        items = []
        reviews = records.list('evidence_review', EvidenceReview)
        for item in records.list('evidence', Evidence):
            if item.claim_revision_id != claim.id:
                continue
            passage = records.get('passage', item.passage_id, Passage)
            document = services.documents.get(passage.document_version_id)
            items.append({'evidence': item, 'passage': passage, 'document': document,
                          'reviews': [review for review in reviews if review.evidence_id == item.id]})
        return {'claim': claim, 'items': items}

    @router.post('/evidence/{evidence_id}/reviews', status_code=201)
    def review(evidence_id: str, data: ReviewWrite):
        item = records.get('evidence', evidence_id, Evidence)
        claim = records.get('claim', item.claim_revision_id, ClaimRevision)
        run = services.runs.get(claim.run_id)
        with services.db.transaction() as conn:
            prior = [r for r in records.list('evidence_review', EvidenceReview, conn=conn) if r.evidence_id == evidence_id]
            revision = max((r.revision for r in prior), default=0)
            if revision != data.expected_revision:
                raise revision_conflict(revision)
            result = EvidenceReview(evidence_id=evidence_id, verdict=data.verdict, reason=data.reason, revision=revision + 1)
            return records.insert('evidence_review', result, project_id=run.project_id, links=[('evidence', item.id)], conn=conn)

    return router
