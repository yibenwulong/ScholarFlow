import pytest
from pydantic import ValidationError

from backend.scholarflow.config import ScholarSettings
from backend.scholarflow.domain.projects import ProjectCreate
from backend.scholarflow.domain.library import PaperCandidate


@pytest.mark.parametrize('payload', [
    {'question': '   '},
    {'question': 'literature', 'year_from': 2025, 'year_to': 2020},
    {'question': 'literature', 'extra': 'unexpected'},
])
def test_rejects_invalid_project_inputs(payload):
    with pytest.raises(ValidationError):
        ProjectCreate(**payload)


def test_project_trims_question_and_preserves_unicode():
    data = ProjectCreate(question='  科研文献比较  ')
    assert data.question == '科研文献比较'
    assert data.language == 'zh-CN'


def test_candidate_without_fulltext_is_not_rejected():
    paper = PaperCandidate(title='A paper', authors=['Alice Example'], source='openalex', source_id='W1')
    assert paper.authors == ['Alice Example']
    assert paper.pdf_url is None


def test_config_can_override_limits_and_uses_workspace_data(monkeypatch, sf_tmp_path):
    monkeypatch.setenv('SCHOLARFLOW_DATA_DIR', str(sf_tmp_path))
    monkeypatch.setenv('SCHOLARFLOW_MAX_INCLUDED_PAPERS', '3')
    settings = ScholarSettings.from_env()
    assert settings.data_dir == sf_tmp_path
    assert settings.max_included_papers == 3
    assert settings.reader_concurrency == 2
    assert settings.max_model_calls == 100


def test_entities_use_aware_utc_timestamps():
    from backend.scholarflow.domain.projects import Project
    project = Project(question='Research')
    assert project.created_at.utcoffset().total_seconds() == 0


def test_domain_import_does_not_load_legacy_model_providers():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        "import sys; import backend.scholarflow.domain.projects; print('gpt_researcher' in sys.modules)"],
        check=True, capture_output=True, text=True)
    assert result.stdout.strip() == 'False'


def test_passage_preserves_original_whitespace_for_quote_offsets():
    from backend.scholarflow.domain.evidence import Passage
    passage = Passage(document_version_id='doc', text='  Result: 42.\n', page_number=1, end=14)
    assert passage.text == '  Result: 42.\n'
