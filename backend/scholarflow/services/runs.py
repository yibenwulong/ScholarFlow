import os

from ..domain.errors import ScholarError, revision_conflict
from ..domain.projects import PlanRevision, SelectionSnapshot
from ..domain.runs import Run, RunCreate
from ..persistence.run_store import RunStore, fingerprint
from .model_gateway import ModelConfig
from .projects import ProjectService


class RunService:
    def __init__(self, db, settings, reports):
        self.db = db
        self.settings = settings
        self.store = RunStore(db)
        self.projects = ProjectService(db)
        self.reports = reports

    def get(self, run_id):
        return self.store.get_run(run_id)

    def list(self, project_id):
        self.projects.get(project_id)
        return self.store.records.list('run', Run, project_id=project_id)

    def _plan(self, run, plan_id):
        if not plan_id:
            raise ScholarError(409, 'approved_plan_required', 'Choose an approved plan for this stage')
        plan = self.projects.approved_plan(run.project_id, plan_id)
        if plan.project_revision != run.request_snapshot['project']['revision']:
            raise ScholarError(409, 'project_changed', 'This plan was created for another project revision; start a new run with a matching plan')
        return plan

    def _selection(self, run, selection_id):
        if not selection_id:
            raise ScholarError(409, 'selection_required', 'Confirm the explicit included paper set first')
        selection = self.store.records.get('selection', selection_id, SelectionSnapshot)
        if selection.project_id != run.project_id:
            raise ScholarError(404, 'selection_not_found', 'Selection does not belong to this project')
        if len(selection.paper_version_ids) > self.settings.max_included_papers:
            raise ScholarError(422, 'selection_limit', 'Selection exceeds the configured paper limit')
        return selection

    def create(self, project_id, data: RunCreate, operation_key):
        scope, digest = f'create_run:{project_id}', fingerprint(data.model_dump())
        with self.db.transaction() as conn:
            previous = conn.execute('SELECT * FROM idempotency WHERE scope=? AND operation_key=?', (scope, operation_key)).fetchone()
            if previous:
                if previous['request_hash'] != digest:
                    raise ScholarError(409, 'idempotency_conflict', 'This key was used for a different run request')
                return self.store.get_run(previous['entity_id'], conn)
            project = self.projects.get(project_id)
            model_name = os.getenv('SCHOLARFLOW_MODEL', os.getenv('SMART_LLM', 'openai:gpt-4o-mini'))
            provider, _, model = model_name.partition(':')
            config = ModelConfig(provider=provider, model=model) if model else ModelConfig(model=provider)
            if self.settings.demo:
                config = ModelConfig(provider='demo', model='synthetic-v1')
            snapshot = {'project': project.model_dump(mode='json'), 'model': config.model_dump(),
                        'limits': {'max_model_calls': self.settings.max_model_calls, 'search_limit': self.settings.search_limit}}
            if data.kind == 'literature_review':
                from ..persistence.projections import Published
                snapshot['human_extractions'] = {pid: item.id for pid, item in Published(self.db).extractions(project_id, conn).items()
                                                 if item.origin == 'human'}
            run = Run(project_id=project_id, **data.model_dump(), request_snapshot=snapshot, stage=data.start_at)
            manifests = {}
            if data.kind == 'literature_review':
                if data.start_at in {'search', 'analyze'}:
                    plan = self._plan(run, data.plan_revision_id)
                    manifests['plan'] = {'plan_revision_id': plan.id}
                if data.start_at == 'analyze':
                    selection = self._selection(run, data.selection_id)
                    manifests['selection'] = {'selection_id': selection.id, 'paper_version_ids': selection.paper_version_ids}
            elif not data.target_id:
                raise ScholarError(422, 'target_required', 'Auxiliary operations require a fixed target ID')
            else:
                from ..domain.operations import ExportJob, ImportJob
                from ..domain.reports import ReportVersion
                kind, model = {'export': ('export', ExportJob), 'import': ('import', ImportJob),
                               'report_check': ('report', ReportVersion)}[data.kind]
                target = self.store.records.get(kind, data.target_id, model, conn)
                if target.project_id != project_id:
                    raise ScholarError(404, 'target_not_found', 'Operation target does not belong to this project')
                if data.kind == 'report_check':
                    from ..persistence.projections import Published
                    if target.id not in Published(self.db).report_ids(project_id):
                        raise ScholarError(404, 'target_not_found', 'Check a published report version')
            run = run.model_copy(update={'manifests': manifests})
            self.store.records.insert('run', run, project_id=project_id, links=[('project', project_id)], conn=conn)
            conn.execute('INSERT INTO idempotency(scope,operation_key,request_hash,entity_id) VALUES (?,?,?,?)',
                         (scope, operation_key, digest, run.id))
            self.store.enqueue(run.id, 'driver', data.kind, {'operation_revision': 0}, operation_key, conn)
            self.store.append_event(run.id, 'queued', run.stage, {}, conn)
            return run

    def act(self, run_id, action, operation_key):
        scope, digest = f'run_action:{run_id}', fingerprint(action.model_dump())
        with self.db.transaction() as conn:
            prior = conn.execute('SELECT * FROM idempotency WHERE scope=? AND operation_key=?', (scope, operation_key)).fetchone()
            if prior:
                if prior['request_hash'] != digest:
                    raise ScholarError(409, 'idempotency_conflict', 'This action key has different inputs')
                return self.store.get_run(run_id, conn)
            run = self.store.get_run(run_id, conn)
            if run.revision != action.expected_revision:
                raise revision_conflict(run.revision)
            if run.status in {'completed', 'cancelled'}:
                raise ScholarError(409, 'run_terminal', 'Create a new run to change completed or cancelled research')
            changes, resume = {}, {}
            if action.action == 'cancel':
                changes = {'status': 'cancelled', 'pending_action': None}
                conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL WHERE run_id=? AND status IN ('queued','running')", (run_id,))
            elif action.action == 'accept_draft':
                pending = run.pending_action or {}
                if run.status != 'needs_review' or pending.get('report_version_id') != action.report_version_id or pending.get('verification_record_id') != action.verification_record_id:
                    raise ScholarError(409, 'draft_target_mismatch', 'Accept the exact report version and verification currently awaiting review')
                acceptance = self.reports.accept_draft(action.report_version_id, action.verification_record_id, conn)
                changes = {'status': 'completed', 'pending_action': None,
                           'manifests': dict(run.manifests, acceptance_id=acceptance.id)}
            elif action.action == 'retry':
                if run.status != 'failed':
                    raise ScholarError(409, 'invalid_run_action', 'Only failed runs can retry their original inputs')
                changes = {'status': 'queued', 'issues': []}
            elif action.action == 'continue':
                pending = run.pending_action or {}
                manifests = dict(run.manifests)
                if run.status == 'waiting_for_user' and pending.get('type') == 'approve_plan':
                    plan = self._plan(run, action.plan_revision_id)
                    manifests['plan'] = {'plan_revision_id': plan.id}
                    resume = {'plan_revision_id': plan.id}
                elif run.status == 'waiting_for_user' and pending.get('type') == 'confirm_selection':
                    if action.plan_revision_id and action.plan_revision_id != manifests['plan']['plan_revision_id']:
                        raise ScholarError(409, 'manifest_frozen', 'The approved plan is fixed; create a new run to change it')
                    selection = self._selection(run, action.selection_id)
                    manifests['selection'] = {'selection_id': selection.id, 'paper_version_ids': selection.paper_version_ids}
                    resume = {'selection_id': selection.id}
                elif run.status == 'needs_review' and pending.get('report_version_id'):
                    report_id = pending['report_version_id']
                    checks = self.reports.store.verifications(report_id, published_only=True)
                    if not checks or checks[0].quality_status != 'passed':
                        raise ScholarError(409, 'review_unresolved', 'Resolve report issues and verify this exact version before continuing')
                    changes = {'status': 'completed', 'quality_status': 'passed', 'pending_action': None}
                else:
                    raise ScholarError(409, 'invalid_run_action', 'This run is not awaiting the supplied action')
                if not changes:
                    changes = {'status': 'queued', 'pending_action': None, 'manifests': manifests}
            result = self.store.update_run(run_id, changes, expected_revision=run.revision, conn=conn)
            if result.status == 'queued':
                self.store.enqueue(run_id, 'driver', run.kind, {'operation_revision': result.revision, 'resume': resume}, operation_key, conn)
            conn.execute('INSERT INTO idempotency(scope,operation_key,request_hash,entity_id) VALUES (?,?,?,?)', (scope, operation_key, digest, run_id))
            self.store.append_event(run_id, 'action', result.stage, {'action': action.action, 'status': result.status}, conn)
            return result
