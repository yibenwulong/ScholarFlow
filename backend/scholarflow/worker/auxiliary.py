from ..domain.errors import ScholarError
from .jobs import ArtifactExecutor


async def drive_auxiliary(services, run, generation):
    store = services.runs.store
    executor = ArtifactExecutor(store, concurrency=services.settings.reader_concurrency)
    async def operation():
        if run.kind == 'export':
            artifact = services.exports.build(run.target_id)
            return artifact.model_dump()
        if run.kind == 'import':
            return await services.imports.build(run.target_id)
        if run.kind == 'report_check':
            record = await services.reports.check(run.target_id, run_id=run.id)
            return {'report_version_id': run.target_id, 'verification_record_id': record.id, 'quality_status': record.quality_status}
        raise ScholarError(422, 'unsupported_operation', 'Unknown auxiliary operation')
    inputs = {'target_id': run.target_id, 'model': run.request_snapshot['model']}
    if run.kind == 'report_check':
        # Explicit rechecks are fresh operations; recovery of this run may reuse its result.
        inputs['check_run_id'] = run.id
    result = await executor.execute(run.id, run.kind, inputs, operation)
    with services.db.transaction() as conn:
        current = store.get_run(run.id, conn)
        if current.execution_generation != generation or current.status == 'cancelled':
            raise ScholarError(409, 'lease_lost', 'This auxiliary execution was cancelled or replaced')
        if run.kind in {'export', 'import'}:
            service = services.exports if run.kind == 'export' else services.imports
            job = service.get(run.target_id, conn)
            changes = {'status': 'ready', 'storage_key': result['storage_key']} if run.kind == 'export' else {'status': 'ready', 'result': result}
            service.records.replace(run.kind, job.model_copy(update=changes), expected_revision=job.revision, conn=conn)
        needs_review = run.kind == 'report_check' and result['quality_status'] != 'passed'
        pending = {'type': 'review_report', 'report_version_id': run.target_id,
                   'verification_record_id': result['verification_record_id']} if needs_review else None
        store.update_run(run.id, {'status': 'needs_review' if needs_review else 'completed',
            'pending_action': pending, 'quality_status': result.get('quality_status', 'unchecked'),
            'manifests': dict(current.manifests, result=result), 'stage': run.kind}, expected_generation=generation, conn=conn)
        store.append_event(run.id, 'finished', run.kind, result, conn)
    return store.get_run(run.id)
