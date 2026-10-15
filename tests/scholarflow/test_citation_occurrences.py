import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.reports import ReportDraft
from backend.scholarflow.services.citations import render_citations
from .test_report_versions import report_context
from .test_extractions import extraction_context
from .test_documents import docs
from .test_library import library


@pytest.mark.asyncio
async def test_same_paper_occurrences_have_one_number_but_independent_bindings(report_context):
    _, version, _, _ = report_context
    rendered = render_citations(version)
    assert rendered.markdown.count('[1]') == 3  # twice in text, once in references
    assert len(rendered.references) == 1
    assert len(version.occurrences) == 2
    assert version.occurrences[0].id != version.occurrences[1].id


@pytest.mark.asyncio
async def test_unknown_keys_are_rejected_even_on_same_source_domain(report_context):
    service, version, _, _ = report_context
    draft = ReportDraft(**version.model_dump(exclude={'id', 'revision', 'created_at', 'project_id', 'previous_id'}))
    draft.blocks[0].text = draft.blocks[0].text.replace('sf_fixture', 'sf_other_arxiv')
    with pytest.raises(ScholarError) as error:
        service.save(version.project_id, draft, version.revision)
    assert error.value.code == 'unknown_citation_key'
