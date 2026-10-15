"""Short SQLite transactions; external work must happen outside this module."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=30000')
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError(f'Database schema {version} is newer than this application')
            if version == 0:
                sql = (Path(__file__).parent / 'migrations' / '001_initial.sql').read_text(encoding='utf-8')
                conn.execute('PRAGMA journal_mode=WAL')
                conn.executescript(sql)
        finally:
            conn.close()

    def schema_version(self) -> int:
        with self.transaction(write=False) as conn:
            return conn.execute('PRAGMA user_version').fetchone()[0]

    @contextmanager
    def transaction(self, *, write: bool = True):
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
