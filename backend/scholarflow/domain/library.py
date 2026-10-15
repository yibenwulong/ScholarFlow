from typing import Literal
from pydantic import Field

from .common import Entity, Model


class PaperCandidate(Model):
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    source: str
    source_id: str
    abstract: str | None = None
    pdf_url: str | None = None
    publication_status: str = 'unknown'
    raw_metadata: dict = Field(default_factory=dict)


class Paper(Entity):
    title: str


class PaperVersion(Entity, PaperCandidate):
    paper_id: str


class SourceRecord(Entity):
    paper_version_id: str
    source: str
    source_id: str
    query: str | None = None
    metadata: dict = Field(default_factory=dict)
    normalized: PaperCandidate | None = None


class ProjectPaper(Entity):
    project_id: str
    paper_id: str
    paper_version_id: str
    human_decision: Literal['pending', 'included', 'excluded'] = 'pending'
    reason: str = ''
    notes: str = ''
    ai_recommendation: Literal['included', 'excluded', 'uncertain'] = 'uncertain'
    ai_reason: str = ''
