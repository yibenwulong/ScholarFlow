from typing import Literal
from pydantic import Field, model_validator

from .common import Entity, Model

ClaimStatus = Literal['supported', 'inferred', 'conflicting', 'insufficient_evidence', 'needs_recheck']


class DocumentVersion(Entity):
    paper_version_id: str
    kind: Literal['pdf', 'abstract']
    sha256: str
    storage_key: str | None = None
    abstract: str | None = None
    source_record_id: str | None = None
    original_name: str = ''
    parse_status: str = 'pending'
    parse_error: str | None = None


class Passage(Entity):
    document_version_id: str
    text: str
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = None
    start: int = Field(default=0, ge=0)
    end: int = Field(ge=0)


class ClaimRevision(Entity):
    run_id: str
    text: str
    type: str = 'finding'
    status: ClaimStatus = 'needs_recheck'
    source_scope: Literal['fulltext', 'abstract']
    previous_id: str | None = None


class Evidence(Entity):
    claim_revision_id: str
    passage_id: str
    quote: str
    relation: Literal['supports', 'refutes', 'insufficient'] = 'supports'
    locator_status: Literal['located', 'unlocated'] = 'unlocated'
    quote_start: int | None = None
    quote_end: int | None = None
    explanation: str = ''


class ExtractedField(Model):
    value: str | None = None
    missing_reason: str | None = None
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    review_status: ClaimStatus = 'needs_recheck'


class ExtractionRevision(Entity):
    project_id: str
    run_id: str | None = None
    paper_version_id: str
    document_version_ids: list[str]
    fields: dict[str, ExtractedField]
    origin: Literal['ai', 'human'] = 'ai'
    previous_id: str | None = None
    coverage: dict = Field(default_factory=dict)


class ReaderOutputDiagnostic(Model):
    loc: list[str | int]
    type: str


class ReaderOutputAudit(Entity):
    project_id: str
    run_id: str
    paper_version_id: str
    document_version_ids: list[str]
    input_hash: str
    reader_version: str = 'reader-v4'
    raw_text: str
    sha256: str
    provider_request_id: str | None = None
    usage: dict | None = None
    output_format: Literal['json', 'json_fence']
    diagnostics: list[ReaderOutputDiagnostic] = Field(default_factory=list)
    normalizations: list[ReaderOutputDiagnostic] = Field(default_factory=list)
    reused_from_audit_id: str | None = None
    origin: Literal['model', 'source_review'] = 'model'
    reviewer: str | None = None
    review_reason: str | None = None

    @model_validator(mode='after')
    def explicit_source_review(self):
        if self.origin == 'source_review':
            if not self.reviewer or not self.reviewer.strip() or not self.review_reason or not self.review_reason.strip():
                raise ValueError('A source review needs a reviewer and explicit reason')
            if (self.provider_request_id is not None or self.usage is not None or self.diagnostics
                    or self.normalizations or self.reused_from_audit_id is not None or self.output_format != 'json'):
                raise ValueError('A source review cannot impersonate or repair a model response')
        return self
