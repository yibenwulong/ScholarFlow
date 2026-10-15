from ..domain.library import PaperVersion, ProjectPaper
from ..domain.errors import not_found
from .records import RecordStore


class PaperStore:
    def __init__(self, db):
        self.records = RecordStore(db)

    def versions(self, conn=None):
        return self.records.list('paper_version', PaperVersion, conn=conn)

    def associations(self, project_id, conn=None):
        return self.records.list('project_paper', ProjectPaper, project_id=project_id, conn=conn)

    def association(self, project_id, paper_id, conn=None):
        for row in self.associations(project_id, conn):
            if row.paper_id == paper_id:
                return row
        raise not_found('project paper')
