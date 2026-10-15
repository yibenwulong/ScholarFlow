import httpx
import pytest
from pathlib import Path
from uuid import uuid4
import shutil

from backend.scholarflow.config import ScholarSettings


@pytest.fixture
def sf_tmp_path():
    # Python 3.13's mkdir(0700) excludes the Windows restricted token. These
    # test-owned directories use inherited workspace ACLs instead.
    base = (Path(__file__).resolve().parents[2] / '.tmp-tests').resolve()
    directory = base / str(uuid4())
    directory.mkdir(parents=True, mode=0o777)
    try:
        yield directory
    finally:
        if directory.resolve().is_relative_to(base):
            shutil.rmtree(directory)


@pytest.fixture
def settings(sf_tmp_path):
    return ScholarSettings(data_dir=sf_tmp_path)


@pytest.fixture
async def api(settings):
    from backend.scholarflow.api.app import create_app
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client
