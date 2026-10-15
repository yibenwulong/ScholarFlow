import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.persistence.file_store import FileStore


def test_file_store_rejects_uncontrolled_paths(sf_tmp_path):
    store = FileStore(sf_tmp_path)
    for key in ('../secret', 'C:/Windows/win.ini', '/etc/passwd', 'ab/../../file'):
        with pytest.raises(ScholarError):
            store.resolve(key)


async def test_file_api_serves_exact_version_and_missing_document_is_404(api, settings):
    from .test_documents import make_pdf
    from backend.scholarflow.services.documents import DocumentService
    from backend.scholarflow.services.library import LibraryService
    from backend.scholarflow.persistence.database import Database
    from backend.scholarflow.domain.library import PaperCandidate
    from backend.scholarflow.domain.projects import ProjectCreate
    db = Database(settings.database_path)
    library = LibraryService(db, settings)
    project = library.projects.create(ProjectCreate(question='Q'))
    paper = library.import_candidates(project.id, [PaperCandidate(title='P', source='local', source_id='1')])[0]
    documents = DocumentService(db, settings)
    content = make_pdf()
    document = documents.import_pdf(project.id, paper.paper_version_id, content, 'paper.pdf')
    documents.parse(document.id)
    response = await api.get(f'/api/v1/documents/{document.id}/file')
    assert response.status_code == 200
    assert response.content == content
    assert response.headers['content-type'].startswith('application/pdf')
    assert (await api.get('/api/v1/documents/missing/file')).status_code == 404
    passages = (await api.get(f'/api/v1/documents/{document.id}/passages')).json()['items']
    assert [p['page_number'] for p in passages] == [1, 2]
    papers = (await api.get(f'/api/v1/projects/{project.id}/papers')).json()['items']
    assert papers[0]['documents'][0]['id'] == document.id
    assert papers[0]['documents'][0]['parse_status'] == 'ready'
