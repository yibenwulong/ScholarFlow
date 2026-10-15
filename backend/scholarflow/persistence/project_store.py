from ..domain.projects import Approval, PlanRevision, Project
from .records import RecordStore


class ProjectStore:
    def __init__(self, db):
        self.records = RecordStore(db)

    def project(self, project_id, conn=None):
        return self.records.get('project', project_id, Project, conn)

    def plans(self, project_id, conn=None):
        return self.records.list('plan', PlanRevision, project_id=project_id, conn=conn)

    def approvals(self, project_id, conn=None):
        return self.records.list('approval', Approval, project_id=project_id, conn=conn)
