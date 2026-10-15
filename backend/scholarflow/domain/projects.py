from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import Entity, Model


class ProjectCreate(Model):
    question: str = Field(min_length=1, max_length=10000)
    language: str = 'zh-CN'
    year_from: int | None = Field(default=None, ge=1000, le=9999)
    year_to: int | None = Field(default=None, ge=1000, le=9999)
    sources: list[Literal['arxiv', 'semantic_scholar', 'openalex']] = Field(
        default_factory=lambda: ['arxiv', 'semantic_scholar', 'openalex'])
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    report_type: Literal['topic_review', 'method_comparison'] = 'topic_review'

    @field_validator('question', mode='before')
    @classmethod
    def trim_question(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def valid_years(self):
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError('year_from must not exceed year_to')
        return self


class Project(Entity, ProjectCreate):
    pass


class PlanDraft(Model):
    queries: list[str] = Field(min_length=1, max_length=20)
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    subquestions: list[str] = Field(default_factory=list)

    @field_validator('queries')
    @classmethod
    def nonempty_queries(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError('Search queries must not be empty')
        return [value.strip() for value in values]


class PlanRevision(Entity, PlanDraft):
    project_id: str
    project_revision: int = Field(default=1, ge=1)


class Approval(Entity):
    project_id: str
    plan_revision_id: str


class SelectionSnapshot(Entity):
    project_id: str
    paper_version_ids: list[str] = Field(min_length=1)
