from typing import Literal
from pydantic import Field, field_validator, model_validator

from .common import Entity, Model


class ReportClaimSpan(Model):
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    token_index: int = Field(ge=0)
    claim_revision_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_span(self):
        if self.end <= self.start:
            raise ValueError('Claim end must be after start')
        return self


class ReportBlock(Model):
    id: str
    heading: str = ''
    text: str
    claims: list[dict] = Field(default_factory=list)

    @field_validator('claims')
    @classmethod
    def validate_spans(cls, values):
        return [ReportClaimSpan.model_validate(value).model_dump() for value in values]


class CitationBinding(Model):
    key: str = Field(pattern=r'^sf_[A-Za-z0-9_]+$')
    paper_version_id: str
    bibliography_snapshot: dict


class CitationOccurrence(Entity):
    block_id: str
    token_index: int
    claim_revision_ids: list[str]
    evidence_ids: list[str]
    claim_start: int | None = None
    claim_end: int | None = None


class ReportDraft(Model):
    run_id: str
    title: str
    blocks: list[ReportBlock] = Field(min_length=1)
    bindings: list[CitationBinding] = Field(default_factory=list)
    occurrences: list[CitationOccurrence] = Field(default_factory=list)
    manifest: dict = Field(default_factory=dict)


class ReportVersion(Entity, ReportDraft):
    project_id: str
    previous_id: str | None = None


class VerificationRecord(Entity):
    project_id: str
    report_version_id: str
    checker: str
    quality_status: Literal['passed', 'issues_present', 'unchecked']
    checks: list[dict]


class DraftAcceptance(Entity):
    project_id: str
    report_version_id: str
    verification_record_id: str


class WriterOutputDiagnostic(Model):
    loc: list[str | int]
    type: str


class WriterOutputAudit(Entity):
    project_id: str
    run_id: str
    raw_text: str
    sha256: str
    provider_request_id: str | None = None
    usage: dict | None = None
    finish_reason: str | None = None
    output_format: Literal['json', 'json_fence']
    parser_version: str = 'writer-json-v1'
    diagnostics: list[WriterOutputDiagnostic] = Field(default_factory=list)
    normalizations: list[WriterOutputDiagnostic] = Field(default_factory=list)
    input_hash: str | None = None
    reused_from_audit_id: str | None = None
