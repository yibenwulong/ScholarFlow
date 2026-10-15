import pytest

from backend.scholarflow.config import ScholarSettings
from backend.scholarflow.domain.library import PaperCandidate
from backend.scholarflow.domain.projects import ProjectCreate
from backend.scholarflow.persistence.database import Database
from backend.scholarflow.services.library import LibraryService


@pytest.fixture
def library(settings):
    db = Database(settings.database_path)
    db.initialize()
    return LibraryService(db, settings)


def candidate(source_id='one', **changes):
    return PaperCandidate(title='A scientific paper', authors=['Author'], source='semantic_scholar',
                          source_id=source_id, **changes)


def test_missing_access_metadata_is_enriched_without_mutating_original_version(library):
    from backend.scholarflow.domain.library import PaperVersion, SourceRecord
    project = library.projects.create(ProjectCreate(question='Q'))
    first = library.import_candidates(project.id, [candidate(identifiers={'doi': '10.1000/enrich'})])[0]
    later = PaperCandidate(title='A scientific paper', source='openalex', source_id='W123',
        identifiers={'doi': '10.1000/enrich'}, abstract='The method uses a graph.', pdf_url='https://example.org/paper.pdf')
    library.import_candidates(project.id, [later])
    row = library.list_project(project.id)[0]
    assert row['version']['abstract'] == later.abstract
    assert row['version']['pdf_url'] == later.pdf_url
    stored = library.store.records.get('paper_version', first.paper_version_id, PaperVersion)
    assert stored.abstract is None and stored.pdf_url is None
    records = library.store.records.list('source_record', SourceRecord)
    assert row['metadata_sources']['abstract'] in [r.id for r in records if r.source == 'openalex']


def test_same_doi_reuses_version_but_preprint_keeps_its_own_version(library):
    project = library.projects.create(ProjectCreate(question='Q'))
    first = library.import_candidates(project.id, [candidate(identifiers={'doi': 'https://doi.org/10.1000/ABC'})])[0]
    duplicate = library.import_candidates(project.id, [candidate('two', identifiers={'doi': '10.1000/abc'})])[0]
    assert duplicate.paper_version_id == first.paper_version_id
    preprint = library.import_candidates(project.id, [candidate('arxiv', identifiers={'doi': '10.1000/abc', 'arxiv': '2401.12345v1'})])[0]
    versions = library.versions(first.paper_id)
    assert len(versions) == 2
    assert {v.identifiers.get('arxiv') for v in versions} == {None, '2401.12345v1'}


def test_title_similarity_does_not_silently_merge_papers(library):
    project = library.projects.create(ProjectCreate(question='Q'))
    rows = library.import_candidates(project.id, [candidate('one'), candidate('two')])
    assert rows[0].paper_id != rows[1].paper_id


def test_rescan_never_overwrites_human_decision(library):
    project = library.projects.create(ProjectCreate(question='Q'))
    paper = library.import_candidates(project.id, [candidate()])[0]
    excluded = library.decide(project.id, paper.paper_id, 'excluded', 'Out of scope', paper.revision)
    library.import_candidates(project.id, [candidate()])
    current = library.association(project.id, paper.paper_id)
    assert current.human_decision == 'excluded'
    assert current.reason == 'Out of scope'
    assert current.revision == excluded.revision
