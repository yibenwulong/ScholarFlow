from dataclasses import dataclass

from ..config import ScholarSettings
from ..persistence.database import Database
from ..services.projects import ProjectService
from ..services.library import LibraryService
from ..services.documents import DocumentService
from ..services.model_gateway import ModelGateway
from ..services.extractions import ExtractionService
from ..services.reports import ReportService
from ..services.runs import RunService
from ..services.exports import ExportService
from ..services.imports import ImportService


@dataclass
class ScholarServices:
    settings: ScholarSettings
    db: Database
    projects: ProjectService
    library: LibraryService
    documents: DocumentService
    gateway: object = None
    extractions: ExtractionService | None = None
    reports: ReportService | None = None
    runs: RunService | None = None
    exports: ExportService | None = None
    imports: ImportService | None = None

    def configure_models(self, gateway):
        self.gateway = gateway
        self.extractions = ExtractionService(self.db, gateway)
        self.reports = ReportService(self.db, gateway)
        self.runs = RunService(self.db, self.settings, self.reports)
        self.exports = ExportService(self.db, self.settings)
        self.imports = ImportService(self.db, self.settings)
        return self

    @classmethod
    def create(cls, settings):
        db = Database(settings.database_path)
        db.initialize()
        return cls(settings, db, ProjectService(db), LibraryService(db, settings), DocumentService(db, settings)).configure_models(
            ModelGateway(db, max_calls=settings.max_model_calls))
