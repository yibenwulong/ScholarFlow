import hashlib

import pymupdf as fitz
import pytest

from backend.scholarflow.domain.library import PaperCandidate, SourceRecord
from backend.scholarflow.domain.projects import ProjectCreate
from backend.scholarflow.services.documents import DocumentService
from .test_library import library


def make_pdf(first='Accuracy was 84 percent.', second='Limitations: synthetic data.', *, encrypted=False):
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 50), first)
        doc.new_page().insert_text((50, 50), second)
        if encrypted:
            return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='fixture-user', owner_pw='fixture-owner')
        return doc.tobytes()


@pytest.fixture
def docs(library):
    project = library.projects.create(ProjectCreate(question='Q'))
    paper = library.import_candidates(project.id, [PaperCandidate(
        title='Synthetic paper', source='local', source_id='fixture', abstract='An abstract result.')])[0]
    service = DocumentService(library.db, library.settings)
    return service, project, paper, library


def test_pdf_pages_preserve_source_and_versions(docs):
    service, project, paper, _ = docs
    content = make_pdf()
    old = service.import_pdf(project.id, paper.paper_version_id, content, '../../paper.pdf')
    passages = service.parse(old.id)
    assert [p.page_number for p in passages] == [1, 2]
    assert passages[0].text.startswith('Accuracy was 84 percent.')
    assert service.parse(old.id)[0].id == passages[0].id
    assert service.import_pdf(project.id, paper.paper_version_id, content, 'again.pdf').id == old.id
    changed = service.import_pdf(project.id, paper.paper_version_id, make_pdf(first='Accuracy was 90 percent.'), 'paper.pdf')
    assert changed.id != old.id
    assert service.get(old.id).sha256 == hashlib.sha256(content).hexdigest()
    assert service.files.resolve(old.storage_key).read_bytes() == content


def test_abstract_has_no_fabricated_page_number(docs):
    service, _, paper, library = docs
    source = library.store.records.list('source_record', SourceRecord)[0]
    document = service.import_abstract(paper.paper_version_id, 'A result only in the abstract.', source.id)
    passage = service.parse(document.id)[0]
    assert passage.page_number is None
    assert passage.text == 'A result only in the abstract.'


@pytest.mark.parametrize('kind,expected', [('blank', 'no_text_layer'), ('corrupt', 'invalid_pdf'), ('encrypted', 'encrypted')])
def test_unreadable_pdf_records_specific_status_without_inventing_passages(docs, kind, expected):
    service, project, paper, _ = docs
    content = b'%PDF-invalid' if kind == 'corrupt' else make_pdf(
        first='' if kind == 'blank' else 'Text', second='', encrypted=kind == 'encrypted')
    document = service.import_pdf(project.id, paper.paper_version_id, content, 'paper.pdf')
    assert service.parse(document.id) == []
    assert service.get(document.id).parse_status == expected


def test_pdf_import_requires_project_membership(docs):
    from backend.scholarflow.domain.errors import ScholarError
    service, _, paper, library = docs
    other = library.projects.create(ProjectCreate(question='Other'))
    with pytest.raises(ScholarError):
        service.import_pdf(other.id, paper.paper_version_id, make_pdf(), 'paper.pdf')


def test_parser_failure_mid_document_does_not_publish_partial_passages(docs, monkeypatch):
    service, project, paper, _ = docs
    document = service.import_pdf(project.id, paper.paper_version_id, make_pdf(), 'paper.pdf')
    original = fitz.Page.get_text
    def fail_second_page(page, *args, **kwargs):
        if page.number == 1:
            raise RuntimeError('Simulated corrupt page')
        return original(page, *args, **kwargs)
    monkeypatch.setattr(fitz.Page, 'get_text', fail_second_page)
    assert service.parse(document.id) == []
    assert service.store.passages(document.id) == []
    assert service.get(document.id).parse_status == 'invalid_pdf'


def test_page_limit_is_checked_before_extraction(docs):
    from dataclasses import replace
    service, project, paper, _ = docs
    service.settings = replace(service.settings, max_pdf_pages=1)
    document = service.import_pdf(project.id, paper.paper_version_id, make_pdf(), 'paper.pdf')
    assert service.parse(document.id) == []
    assert service.get(document.id).parse_status == 'page_limit_exceeded'
