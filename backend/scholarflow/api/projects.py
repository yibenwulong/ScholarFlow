from fastapi import APIRouter, Query
from pydantic import Field

from ..domain.common import Model
from ..domain.projects import PlanDraft, ProjectCreate


class ProjectPatch(Model):
    expected_revision: int = Field(ge=1)
    question: str | None = None
    language: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    sources: list[str] | None = None
    inclusion_criteria: list[str] | None = None
    exclusion_criteria: list[str] | None = None
    report_type: str | None = None


class PlanWrite(PlanDraft):
    expected_revision: int = Field(ge=0)


class ApprovalWrite(Model):
    expected_revision: int = Field(ge=1)


def page(items, cursor=0, limit=100):
    return {'items': items[cursor:cursor + limit],
            'next_cursor': str(cursor + limit) if cursor + limit < len(items) else None}


def project_router(services):
    router = APIRouter()

    @router.post('/projects', status_code=201)
    def create_project(data: ProjectCreate):
        return services.projects.create(data)

    @router.get('/projects')
    def list_projects(cursor: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000)):
        return page(services.projects.list(), cursor, limit)

    @router.get('/projects/{project_id}')
    def get_project(project_id: str):
        return services.projects.get(project_id)

    @router.patch('/projects/{project_id}')
    def update_project(project_id: str, data: ProjectPatch):
        changes = data.model_dump(exclude_unset=True, exclude={'expected_revision'})
        return services.projects.update(project_id, changes, data.expected_revision)

    @router.get('/projects/{project_id}/plans')
    def list_plans(project_id: str):
        return page(services.projects.list_plans(project_id))

    @router.post('/projects/{project_id}/plans', status_code=201)
    def save_plan(project_id: str, data: PlanWrite):
        draft = PlanDraft.model_validate(data.model_dump(exclude={'expected_revision'}))
        return services.projects.save_plan(project_id, draft, data.expected_revision)

    @router.post('/projects/{project_id}/plans/{plan_id}/approve')
    def approve_plan(project_id: str, plan_id: str, data: ApprovalWrite):
        return services.projects.approve_plan(project_id, plan_id, data.expected_revision)

    return router
