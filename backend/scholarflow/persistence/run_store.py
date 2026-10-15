from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import json
import time
from uuid import uuid4

from ..domain.errors import ScholarError, not_found, revision_conflict
from ..domain.runs import Run, RunEvent
from .records import RecordStore


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Job:
    id: str
    run_id: str
    kind: str
    stage: str
    input_hash: str
    input_manifest: dict
    status: str
    result: dict | None
    error: dict | None
    attempt_no: int
    lease_owner: str | None
    lease_expires_at: float | None
    generation: int


@dataclass(frozen=True)
class JobLease:
    id: str
    run_id: str
    kind: str
    stage: str
    input_manifest: dict
    input_hash: str
    attempt_no: int
    lease_owner: str
    lease_expires_at: float
    ttl_seconds: int
    generation: int


def parse_job(row):
    payload = json.loads(row['payload'])
    return Job(row['id'], row['run_id'], row['kind'], row['stage'], row['input_hash'], payload['manifest'],
               row['status'], json.loads(row['result']) if row['result'] is not None else None,
               json.loads(row['error']) if row['error'] is not None else None,
               row['attempt_no'], row['lease_owner'], row['lease_expires_at'], payload['generation'])


class RunStore:
    def __init__(self, db):
        self.db = db
        self.records = RecordStore(db)

    def get_run(self, run_id, conn=None):
        return self.records.get('run', run_id, Run, conn)

    def get_job(self, job_id, conn=None):
        with (nullcontext(conn) if conn is not None else self.db.transaction(write=False)) as cx:
            row = cx.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise not_found('job')
            return parse_job(row)

    def list_jobs(self, run_id):
        self.get_run(run_id)
        with self.db.transaction(write=False) as conn:
            return [parse_job(row) for row in conn.execute('SELECT * FROM jobs WHERE run_id=? ORDER BY created_at,id', (run_id,))]

    def enqueue(self, run_id, kind, stage, input_manifest, operation_key, conn=None):
        digest = fingerprint(input_manifest)
        scope = f'job:{run_id}:{kind}:{stage}'
        now = time.time()
        with (nullcontext(conn) if conn is not None else self.db.transaction()) as conn:
            run = self.get_run(run_id, conn)
            if run.status in {'completed', 'cancelled'}:
                raise ScholarError(409, 'run_terminal', 'Create a new run for this operation')
            previous = conn.execute('SELECT request_hash FROM idempotency WHERE scope=? AND operation_key=?',
                                    (scope, operation_key)).fetchone()
            if previous and previous['request_hash'] != digest:
                raise ScholarError(409, 'idempotency_conflict', 'This operation key was used for different inputs')
            row = conn.execute('SELECT * FROM jobs WHERE run_id=? AND kind=? AND stage=? AND input_hash=?',
                               (run_id, kind, stage, digest)).fetchone()
            if row is None:
                job_id = str(uuid4())
                payload = canonical({'manifest': input_manifest, 'generation': run.execution_generation})
                conn.execute('INSERT INTO jobs(id,run_id,kind,stage,input_hash,payload,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                             (job_id, run_id, kind, stage, digest, payload, now, now))
                row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            conn.execute('INSERT OR IGNORE INTO idempotency(scope,operation_key,request_hash,entity_id) VALUES (?,?,?,?)',
                         (scope, operation_key, digest, run_id))
            return parse_job(row)

    def claim_next_driver(self, owner, now=None, ttl_seconds=60):
        now = time.time() if now is None else now
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE kind='driver' AND (status='queued' OR "
                                "(status='running' AND lease_expires_at<=?)) ORDER BY created_at,id", (now,)).fetchall()
            for row in rows:
                run = self.get_run(row['run_id'], conn)
                if run.status == 'cancelled':
                    continue
                takeover = row['status'] == 'running'
                run = run.model_copy(update={'status': 'running', 'stage': row['stage'],
                    'execution_generation': run.execution_generation + int(takeover)})
                self.records.replace('run', run, expected_revision=run.revision, conn=conn)
                return self._claim(row, run, owner, now, ttl_seconds, conn)
        return None

    def claim_artifact(self, job_id, owner, now=None, ttl_seconds=60):
        now = time.time() if now is None else now
        with self.db.transaction() as conn:
            job = self.get_job(job_id, conn)
            run = self.get_run(job.run_id, conn)
            if job.kind != 'artifact' or job.status == 'succeeded' or run.status == 'cancelled':
                return None
            if (job.status == 'running' and job.lease_expires_at > now
                    and job.generation == run.execution_generation):
                return None
            row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            return self._claim(row, run, owner, now, ttl_seconds, conn)

    def _claim(self, row, run, owner, now, ttl_seconds, conn):
        payload = json.loads(row['payload'])
        payload['generation'] = run.execution_generation
        attempt = row['attempt_no'] + 1
        expiry = now + ttl_seconds
        conn.execute("UPDATE jobs SET status='running',attempt_no=?,lease_owner=?,lease_expires_at=?,payload=?,updated_at=? WHERE id=?",
                     (attempt, owner, expiry, canonical(payload), now, row['id']))
        return JobLease(row['id'], row['run_id'], row['kind'], row['stage'], payload['manifest'],
                        row['input_hash'], attempt, owner, expiry, ttl_seconds, run.execution_generation)

    def _owns(self, lease, now, conn):
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (lease.id,)).fetchone()
        if row is None:
            return False
        run = self.get_run(lease.run_id, conn)
        return (row['status'] == 'running' and row['lease_owner'] == lease.lease_owner
                and row['attempt_no'] == lease.attempt_no and row['lease_expires_at'] > now
                and run.execution_generation == lease.generation and run.status != 'cancelled')

    def heartbeat(self, lease, now=None):
        now = time.time() if now is None else now
        with self.db.transaction() as conn:
            if not self._owns(lease, now, conn):
                return False
            conn.execute('UPDATE jobs SET lease_expires_at=?,updated_at=? WHERE id=?',
                         (now + lease.ttl_seconds, now, lease.id))
            return True

    def finish(self, lease, result, now=None):
        now = time.time() if now is None else now
        encoded = canonical(result)
        with self.db.transaction() as conn:
            if not self._owns(lease, now, conn):
                return False
            conn.execute("UPDATE jobs SET status='succeeded',result=?,error=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?",
                         (encoded, now, lease.id))
            return True

    def fail(self, lease, error, *, retry=False, now=None):
        now = time.time() if now is None else now
        with self.db.transaction() as conn:
            if not self._owns(lease, now, conn):
                return False
            conn.execute('UPDATE jobs SET status=?,error=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?',
                         ('queued' if retry else 'failed', canonical(error), now, lease.id))
            return True

    def update_run(self, run_id, changes, *, expected_generation=None, expected_revision=None, conn=None):
        with (nullcontext(conn) if conn is not None else self.db.transaction()) as cx:
            current = self.get_run(run_id, cx)
            if expected_generation is not None and current.execution_generation != expected_generation:
                raise ScholarError(409, 'lease_lost', 'This execution was replaced by a newer worker')
            if current.status == 'cancelled' and changes.get('status', 'cancelled') != 'cancelled':
                raise ScholarError(409, 'run_cancelled', 'This run was cancelled')
            if 'request_snapshot' in changes and changes['request_snapshot'] != current.request_snapshot:
                raise ScholarError(409, 'snapshot_frozen', 'Run inputs are immutable')
            updated = Run.model_validate(dict(current.model_dump(), **changes))
            return self.records.replace('run', updated, expected_revision=current.revision if expected_revision is None else expected_revision, conn=cx)

    def put_manifest(self, run_id, name, value, *, generation):
        with self.db.transaction() as conn:
            current = self.get_run(run_id, conn)
            if name in current.manifests and current.manifests[name] != value:
                raise ScholarError(409, 'manifest_frozen', 'A stage input has already been fixed')
            manifests = dict(current.manifests)
            manifests[name] = value
            return self.update_run(run_id, {'manifests': manifests}, expected_generation=generation, conn=conn)

    def cancel_run(self, run_id):
        with self.db.transaction() as conn:
            run = self.get_run(run_id, conn)
            if run.status == 'completed':
                raise ScholarError(409, 'run_terminal', 'The run has already completed')
            updated = self.update_run(run_id, {'status': 'cancelled', 'pending_action': None}, conn=conn)
            conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL WHERE run_id=? AND status IN ('queued','running')", (run_id,))
            return updated

    def append_event(self, run_id, event_type, stage, data, conn=None):
        with (nullcontext(conn) if conn is not None else self.db.transaction()) as cx:
            self.get_run(run_id, cx)
            seq = cx.execute('SELECT COALESCE(MAX(seq),0)+1 FROM run_events WHERE run_id=?', (run_id,)).fetchone()[0]
            event = RunEvent(run_id=run_id, seq=seq, type=event_type, stage=stage, data=data)
            cx.execute('INSERT INTO run_events(run_id,seq,payload) VALUES (?,?,?)', (run_id, seq, event.model_dump_json()))
            return event

    def events(self, run_id, after=0):
        self.get_run(run_id)
        with self.db.transaction(write=False) as conn:
            return [RunEvent.model_validate_json(row['payload']) for row in conn.execute(
                'SELECT payload FROM run_events WHERE run_id=? AND seq>? ORDER BY seq', (run_id, after))]

    def cached_artifact(self, stage, digest, project_id):
        with self.db.transaction(write=False) as conn:
            row = conn.execute("SELECT j.* FROM jobs j JOIN entities e ON e.id=j.run_id WHERE j.kind='artifact' "
                               "AND j.stage=? AND j.input_hash=? AND j.status='succeeded' AND e.project_id=? "
                               'ORDER BY j.updated_at DESC LIMIT 1', (stage, digest, project_id)).fetchone()
            return parse_job(row) if row else None
