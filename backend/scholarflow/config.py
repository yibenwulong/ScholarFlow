"""Instance configuration. Credentials remain in the server environment."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class ScholarSettings:
    data_dir: Path
    max_included_papers: int = 20
    search_limit: int = 20
    reader_concurrency: int = 2
    max_model_calls: int = 100
    max_file_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    demo: bool = False

    @classmethod
    def from_env(cls) -> 'ScholarSettings':
        values = {'data_dir': Path(os.getenv('SCHOLARFLOW_DATA_DIR', 'data/scholarflow')).resolve()}
        for name in ('max_included_papers', 'search_limit', 'reader_concurrency', 'max_model_calls',
                     'max_file_bytes', 'max_pdf_pages', 'lease_seconds', 'heartbeat_seconds'):
            raw = os.getenv('SCHOLARFLOW_' + name.upper())
            if raw is not None:
                value = int(raw)
                if value < 1:
                    raise ValueError(f'{name} must be positive')
                values[name] = value
        values['demo'] = os.getenv('SCHOLARFLOW_DEMO', '').lower() == 'true'
        return cls(**values)

    @property
    def database_path(self) -> Path:
        return self.data_dir / 'scholarflow.sqlite3'

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / 'checkpoints.sqlite3'
