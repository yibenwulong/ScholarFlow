import sqlite3

import pytest

from backend.scholarflow.persistence.database import Database


def test_migrations_are_repeatable_and_persist_records(sf_tmp_path):
    path = sf_tmp_path / 'scholarflow.db'
    db = Database(path)
    db.initialize()
    db.initialize()
    with db.transaction() as conn:
        conn.execute("INSERT INTO entities(id, kind, payload) VALUES ('project-1', 'project', '{}')")
    reopened = Database(path)
    reopened.initialize()
    assert reopened.schema_version() == 1
    with reopened.transaction() as conn:
        assert conn.execute('SELECT id FROM entities').fetchone()[0] == 'project-1'


def test_links_cannot_reference_a_missing_entity(sf_tmp_path):
    db = Database(sf_tmp_path / 'db.sqlite3')
    db.initialize()
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute("INSERT INTO entity_links(owner_id, relation, target_id) VALUES ('absent', 'source', 'missing')")


def test_failed_transaction_rolls_back(sf_tmp_path):
    db = Database(sf_tmp_path / 'db.sqlite3')
    db.initialize()
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO entities(id, kind, payload) VALUES ('rollback', 'project', '{}')")
        raise RuntimeError('abort')
    with db.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entities').fetchone()[0] == 0
