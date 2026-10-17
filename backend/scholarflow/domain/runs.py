from typing import Literal
from pydantic import Field

from .common import Entity, Model

RunStatus = Literal['queued', 'running', 'waiting_for_user', 'needs_review', 'completed', 'failed', 'cancelled']


class RunCreate(Model):
    kind: Literal['literature_review', 'import', 'report_check', 'export', 'paper_read'] = 'literature_review'
    start_at: Literal['plan', 'search', 'analyze'] = 'plan'
    plan_revision_id: str | None = None
    selection_id: str | None = None
    target_id: str | None = None


class Run(Entity, RunCreate):
    project_id: str
    status: RunStatus = 'queued'
    stage: str = 'plan'
    quality_status: str = 'unchecked'
    request_snapshot: dict = Field(default_factory=dict)
    manifests: dict = Field(default_factory=dict)
    pending_action: dict | None = None
    issues: list[dict] = Field(default_factory=list)
    execution_generation: int = 1
    model_calls: int = 0


class RunAction(Model):
    action: Literal['continue', 'retry', 'cancel', 'accept_draft']
    expected_revision: int
    plan_revision_id: str | None = None
    selection_id: str | None = None
    report_version_id: str | None = None
    verification_record_id: str | None = None


class RunEvent(Entity):
    run_id: str
    seq: int
    type: str
    stage: str
    data: dict = Field(default_factory=dict)
