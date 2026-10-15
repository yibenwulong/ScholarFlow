"""Typed entity envelopes with explicit foreign-key provenance links."""
from contextlib import nullcontext
import json

from ..domain.errors import not_found, revision_conflict
from .database import Database

MUTABLE_KINDS = {'project', 'project_paper', 'document', 'run', 'export', 'import'}


class RecordStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self, kind, entity_id, model, conn=None):
        with (nullcontext(conn) if conn is not None else self.db.transaction(write=False)) as cx:
            row = cx.execute('SELECT payload FROM entities WHERE id=? AND kind=?', (entity_id, kind)).fetchone()
            if row is None:
                raise not_found(kind)
            return model.model_validate_json(row['payload'])

    def list(self, kind, model, *, project_id=None, conn=None):
        with (nullcontext(conn) if conn is not None else self.db.transaction(write=False)) as cx:
            sql = 'SELECT payload FROM entities WHERE kind=?'
            args = [kind]
            if project_id is not None:
                sql += ' AND project_id=?'
                args.append(project_id)
            sql += ' ORDER BY created_at DESC, id DESC'
            return [model.model_validate_json(row['payload']) for row in cx.execute(sql, args)]

    def insert(self, kind, entity, *, project_id=None, links=(), conn=None):
        with (nullcontext(conn) if conn is not None else self.db.transaction()) as cx:
            cx.execute('INSERT INTO entities(id,kind,project_id,revision,payload,created_at) VALUES (?,?,?,?,?,?)',
                       (entity.id, kind, project_id, entity.revision, entity.model_dump_json(), entity.created_at.isoformat()))
            for relation, target in links:
                cx.execute('INSERT INTO entity_links(owner_id,relation,target_id) VALUES (?,?,?)',
                           (entity.id, relation, target))
            return entity

    def replace(self, kind, entity, *, expected_revision, conn=None):
        if kind not in MUTABLE_KINDS:
            raise ValueError(f'{kind} is immutable; create a revision')
        with (nullcontext(conn) if conn is not None else self.db.transaction()) as cx:
            current = cx.execute('SELECT revision FROM entities WHERE id=? AND kind=?', (entity.id, kind)).fetchone()
            if current is None:
                raise not_found(kind)
            if current['revision'] != expected_revision:
                raise revision_conflict(current['revision'])
            updated = entity.model_copy(update={'revision': expected_revision + 1})
            cx.execute('UPDATE entities SET revision=?, payload=? WHERE id=? AND kind=? AND revision=?',
                       (updated.revision, updated.model_dump_json(), entity.id, kind, expected_revision))
            return updated

    def next_revision(self, kind, project_id, conn):
        row = conn.execute('SELECT COALESCE(MAX(revision),0) FROM entities WHERE kind=? AND project_id=?',
                           (kind, project_id)).fetchone()
        return row[0] + 1
