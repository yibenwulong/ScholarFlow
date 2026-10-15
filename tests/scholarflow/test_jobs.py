from dataclasses import replace

import pytest

from backend.scholarflow.domain.errors import ScholarError
from backend.scholarflow.domain.projects import ProjectCreate
from backend.scholarflow.domain.runs import Run
from backend.scholarflow.persistence.database import Database
from backend.scholarflow.persistence.run_store import RunStore
from backend.scholarflow.services.projects import ProjectService


@pytest.fixture
def job_context(settings):
    db = Database(settings.database_path)
    db.initialize()
    project = ProjectService(db).create(ProjectCreate(question='Q'))
    store = RunStore(db)
    run = Run(project_id=project.id)
    store.records.insert('run', run, project_id=project.id)
    return store, run


def test_operation_keys_reuse_results_and_reject_different_bodies(job_context):
    store, run = job_context
    original = store.enqueue(run.id, 'driver', 'research', {'document': 'a'}, 'key-a')
    assert store.enqueue(run.id, 'driver', 'research', {'document': 'a'}, 'key-a').id == original.id
    assert store.enqueue(run.id, 'driver', 'research', {'document': 'a'}, 'key-b').id == original.id
    with pytest.raises(ScholarError) as failure:
        store.enqueue(run.id, 'driver', 'research', {'document': 'changed'}, 'key-b')
    assert failure.value.code == 'idempotency_conflict'


def test_expired_driver_is_reclaimed_and_old_attempt_cannot_commit(job_context):
    store, run = job_context
    job = store.enqueue(run.id, 'driver', 'research', {}, 'key')
    old = store.claim_next_driver('old-worker', now=10, ttl_seconds=5)
    new = store.claim_next_driver('new-worker', now=16, ttl_seconds=5)
    assert new.id == job.id
    assert new.attempt_no == 2
    assert store.get_run(run.id).execution_generation == 2
    assert store.finish(old, {'report': 'stale'}, now=17) is False
    assert store.finish(new, {'report': 'current'}, now=17) is True
    assert store.get_job(job.id).result == {'report': 'current'}


def test_renewal_and_completion_require_current_owner_and_unexpired_lease(job_context):
    store, run = job_context
    store.enqueue(run.id, 'driver', 'research', {}, 'key')
    lease = store.claim_next_driver('worker', now=10, ttl_seconds=5)
    assert store.heartbeat(lease, now=14) is True
    assert store.finish(replace(lease, lease_owner='other'), {}, now=16) is False
    assert store.finish(lease, {'ok': True}, now=16) is True


def test_cancelled_run_never_publishes_late_job_result(job_context):
    store, run = job_context
    job = store.enqueue(run.id, 'driver', 'research', {}, 'key')
    lease = store.claim_next_driver('worker', now=10, ttl_seconds=10)
    store.cancel_run(run.id)
    assert store.finish(lease, {'report': 'late'}, now=11) is False
    assert store.get_job(job.id).result is None
    assert store.get_run(run.id).status == 'cancelled'


def test_takeover_fences_old_artifact_even_if_its_own_lease_has_not_expired(job_context):
    store, run = job_context
    store.enqueue(run.id, 'driver', 'research', {}, 'driver')
    artifact = store.enqueue(run.id, 'artifact', 'read', {'doc': 'one'}, 'artifact')
    store.claim_next_driver('old', now=10, ttl_seconds=5)
    old_artifact = store.claim_artifact(artifact.id, 'old', now=11, ttl_seconds=100)
    store.claim_next_driver('new', now=16, ttl_seconds=10)
    new_artifact = store.claim_artifact(artifact.id, 'new', now=17, ttl_seconds=10)
    assert new_artifact is not None
    assert new_artifact.attempt_no == 2
    assert store.finish(old_artifact, {'answer': 'old'}, now=18) is False
    assert store.finish(new_artifact, {'answer': 'new'}, now=18) is True
