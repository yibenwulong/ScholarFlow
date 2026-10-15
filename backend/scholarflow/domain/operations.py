from typing import Literal
from pydantic import Field

from .common import Entity, Model
from .library import PaperCandidate

ExportFormat = Literal['markdown', 'docx', 'bibtex']


class ExportJob(Entity):
    project_id: str
    report_version_id: str
    format: ExportFormat
    verification_id: str
    draft_acceptance_id: str | None = None
    document_version_ids: list[str]
    draft: bool
    status: str = 'queued'
    storage_key: str | None = None


class ExportArtifact(Model):
    report_version_id: str
    storage_key: str
    format: ExportFormat
    size: int


class ImportWrite(Model):
    candidates: list[PaperCandidate] = Field(default_factory=list, max_length=100)
    identifiers: dict[str, str] = Field(default_factory=dict)


class ImportJob(Entity):
    project_id: str
    candidates: list[PaperCandidate] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    paper_version_id: str | None = None
    upload_key: str | None = None
    original_name: str = ''
    status: str = 'queued'
    result: dict | None = None


class EvidenceReview(Entity):
    evidence_id: str
    verdict: Literal['supported', 'inferred', 'conflicting', 'insufficient_evidence', 'needs_recheck']
    reason: str = Field(min_length=1)
    reviewer: str = 'local_user'
