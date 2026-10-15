"""A lightweight ASGI entrypoint for local development and offline tests."""
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..config import ScholarSettings
from ..domain.errors import ScholarError
from .dependencies import ScholarServices
from .projects import project_router
from .library import library_router
from .runs import run_router
from .evidence import evidence_router
from .reports import report_router


def install_error_handlers(app, *, prefix=None):
    @app.exception_handler(ScholarError)
    async def scholar_error(request, exc):
        return JSONResponse(status_code=exc.status, content={'error': {
            'code': exc.code, 'message': exc.message, 'retryable': exc.retryable, 'details': exc.details}})

    async def validation_error(request, exc):
        if prefix and not request.url.path.startswith(prefix):
            if isinstance(exc, RequestValidationError):
                from fastapi.exception_handlers import request_validation_exception_handler
                return await request_validation_exception_handler(request, exc)
            raise exc
        details = [{'field': list(item['loc']), 'message': item['msg'], 'type': item['type']}
                   for item in exc.errors()]
        return JSONResponse(status_code=422, content={'error': {
            'code': 'validation_error', 'message': 'Invalid request', 'retryable': False, 'details': details}})

    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(ValidationError, validation_error)


def create_router(services: ScholarServices) -> APIRouter:
    router = APIRouter(prefix='/api/v1')
    @router.get('/status')
    def status():
        return {'demo': services.settings.demo, 'max_included_papers': services.settings.max_included_papers,
                'max_model_calls': services.settings.max_model_calls}
    router.include_router(project_router(services))
    router.include_router(library_router(services))
    router.include_router(run_router(services))
    router.include_router(evidence_router(services))
    router.include_router(report_router(services))
    return router


def create_app(settings: ScholarSettings | None = None) -> FastAPI:
    services = ScholarServices.create(settings or ScholarSettings.from_env())

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(title='ScholarFlow', lifespan=lifespan)
    app.state.scholarflow = services
    install_error_handlers(app)
    app.include_router(create_router(services))
    return app
