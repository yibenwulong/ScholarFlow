from ..domain.errors import not_found, revision_conflict
from ..domain.projects import Approval, PlanDraft, PlanRevision, Project, ProjectCreate
from ..persistence.project_store import ProjectStore


class ProjectService:
    def __init__(self, db):
        self.db = db
        self.store = ProjectStore(db)

    def create(self, data: ProjectCreate) -> Project:
        project = Project(**data.model_dump())
        return self.store.records.insert('project', project)

    def get(self, project_id) -> Project:
        return self.store.project(project_id)

    def list(self) -> list[Project]:
        return self.store.records.list('project', Project)

    def update(self, project_id, changes, expected_revision) -> Project:
        with self.db.transaction() as conn:
            current = self.store.project(project_id, conn)
            values = current.model_dump()
            values.update(changes)
            updated = Project.model_validate(values)
            return self.store.records.replace('project', updated, expected_revision=expected_revision, conn=conn)

    def save_plan(self, project_id, draft: PlanDraft, expected_revision) -> PlanRevision:
        with self.db.transaction() as conn:
            project = self.store.project(project_id, conn)
            next_revision = self.store.records.next_revision('plan', project_id, conn)
            if expected_revision != next_revision - 1:
                raise revision_conflict(next_revision - 1)
            plan = PlanRevision(project_id=project_id, revision=next_revision,
                                project_revision=project.revision, **draft.model_dump())
            return self.store.records.insert('plan', plan, project_id=project_id,
                                             links=[('project', project_id)], conn=conn)

    def approve_plan(self, project_id, plan_revision_id, expected_revision) -> Approval:
        with self.db.transaction() as conn:
            self.store.project(project_id, conn)
            plan = self.store.records.get('plan', plan_revision_id, PlanRevision, conn)
            if plan.project_id != project_id:
                raise not_found('plan')
            if plan.revision != expected_revision:
                raise revision_conflict(plan.revision)
            for existing in self.store.approvals(project_id, conn):
                if existing.plan_revision_id == plan.id:
                    return existing
            approval = Approval(project_id=project_id, plan_revision_id=plan.id)
            return self.store.records.insert('approval', approval, project_id=project_id,
                                             links=[('plan', plan.id)], conn=conn)

    def list_plans(self, project_id):
        self.get(project_id)
        approved = {a.plan_revision_id for a in self.store.approvals(project_id)}
        return [dict(plan.model_dump(mode='json'), approved=plan.id in approved)
                for plan in self.store.plans(project_id)]

    def approved_plan(self, project_id, plan_id) -> PlanRevision:
        plan = self.store.records.get('plan', plan_id, PlanRevision)
        if plan.project_id != project_id:
            raise not_found('plan')
        if plan_id not in {a.plan_revision_id for a in self.store.approvals(project_id)}:
            from ..domain.errors import ScholarError
            raise ScholarError(409, 'plan_not_approved', 'Approve the selected search plan first')
        return plan
