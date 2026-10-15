import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.projects import ProjectCreate
from .test_library import candidate, library


def test_confirmed_subset_does_not_require_deciding_all_candidates(library):
    project = library.projects.create(ProjectCreate(question='Q'))
    first, other = library.import_candidates(project.id, [candidate('one'), candidate('two')])
    snapshot = library.freeze_selection(project.id, [first.paper_version_id], 0)
    assert snapshot.paper_version_ids == [first.paper_version_id]
    assert library.association(project.id, other.paper_id).human_decision == 'pending'
    assert library.association(project.id, first.paper_id).human_decision == 'included'


def test_excluded_and_cross_project_versions_cannot_be_selected(library):
    a = library.projects.create(ProjectCreate(question='A'))
    b = library.projects.create(ProjectCreate(question='B'))
    paper = library.import_candidates(a.id, [candidate()])[0]
    library.decide(a.id, paper.paper_id, 'excluded', 'Wrong population', paper.revision)
    for project_id in (a.id, b.id):
        with pytest.raises(ScholarError):
            library.freeze_selection(project_id, [paper.paper_version_id], 0)


def test_selection_rejects_empty_duplicate_and_stale_inputs(library):
    project = library.projects.create(ProjectCreate(question='Q'))
    paper = library.import_candidates(project.id, [candidate()])[0]
    for ids in ([], [paper.paper_version_id, paper.paper_version_id]):
        with pytest.raises(ScholarError):
            library.freeze_selection(project.id, ids, 0)
    library.freeze_selection(project.id, [paper.paper_version_id], 0)
    with pytest.raises(ScholarError) as failure:
        library.freeze_selection(project.id, [paper.paper_version_id], 0)
    assert failure.value.code == 'revision_conflict'


def test_default_selection_limit_rejects_twenty_one_papers(library):
    project = library.projects.create(ProjectCreate(question='Q'))
    rows = library.import_candidates(project.id, [candidate(str(i)) for i in range(21)])
    with pytest.raises(ScholarError) as failure:
        library.freeze_selection(project.id, [row.paper_version_id for row in rows], 0)
    assert failure.value.code == 'selection_size'


async def test_paper_api_retains_metadata_and_enforces_exclusion(api, library):
    project = library.projects.create(ProjectCreate(question='Q'))
    paper = library.import_candidates(project.id, [candidate(year=2024)])[0]
    base = f'/api/v1/projects/{project.id}'
    listing = (await api.get(base + '/papers')).json()['items']
    assert listing[0]['version']['authors'] == ['Author']
    assert listing[0]['version']['year'] == 2024
    excluded = await api.patch(base + f'/papers/{paper.paper_id}', json={
        'expected_revision': 1, 'decision': 'excluded', 'reason': 'Not relevant'})
    assert excluded.status_code == 200
    selection = await api.post(base + '/selections', json={
        'expected_revision': 0, 'paper_version_ids': [paper.paper_version_id]})
    assert selection.status_code == 409
