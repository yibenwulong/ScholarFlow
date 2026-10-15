from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4

from ..domain.errors import ScholarError, not_found


@dataclass(frozen=True)
class StoredFile:
    key: str
    sha256: str
    size: int


class FileStore:
    def __init__(self, data_dir: Path):
        self.root = (Path(data_dir) / 'documents').resolve()

    def put(self, data: bytes) -> StoredFile:
        digest = hashlib.sha256(data).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / (digest + '.pdf')
        if not destination.exists():
            temporary = self.root / ('.' + str(uuid4()) + '.tmp')
            try:
                temporary.write_bytes(data)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return StoredFile(digest, digest, len(data))

    def resolve(self, storage_key: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{64}', storage_key):
            raise ScholarError(422, 'invalid_storage_key', 'Invalid file identifier')
        path = (self.root / (storage_key + '.pdf')).resolve()
        if not path.is_relative_to(self.root):
            raise ScholarError(422, 'invalid_storage_key', 'File is outside the instance directory')
        if not path.is_file():
            raise not_found('document file')
        return path
