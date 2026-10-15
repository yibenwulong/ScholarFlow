import re

from ..domain.errors import ScholarError, not_found, revision_conflict
from ..domain.library import Paper, PaperCandidate, PaperVersion, ProjectPaper, SourceRecord
from ..domain.projects import SelectionSnapshot
from ..persistence.paper_store import PaperStore
from ..persistence.evidence_store import EvidenceStore
from .projects import ProjectService
from .providers.base import normalize_identifiers


def version_identity(candidate):
    ids = candidate.identifiers
    if ids.get('arxiv'):
        return ('arxiv', ids['arxiv'])
    if ids.get('doi'):
        return ('doi', ids['doi'])
    return (candidate.source, candidate.source_id)


def related(a, b):
    if a.identifiers.get('doi') and a.identifiers.get('doi') == b.identifiers.get('doi'):
        return True
    left, right = a.identifiers.get('arxiv'), b.identifiers.get('arxiv')
    return bool(left and right and re.sub(r'v\d+$', '', left) == re.sub(r'v\d+$', '', right))


class LibraryService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.projects = ProjectService(db)
        self.store = PaperStore(db)

    def import_candidates(self, project_id, candidates, *, query=None):
        result = []
        with self.db.transaction() as conn:
            self.projects.store.project(project_id, conn)
            versions = self.store.versions(conn)
            for raw in candidates:
                candidate = raw.model_copy(update={'identifiers': normalize_identifiers(raw.identifiers)})
                version = next((v for v in versions if version_identity(v) == version_identity(candidate)), None)
                if version is None:
                    known = next((v for v in versions if related(v, candidate)), None)
                    if known:
                        paper_id = known.paper_id
                    else:
                        paper = self.store.records.insert('paper', Paper(title=candidate.title), conn=conn)
                        paper_id = paper.id
                    version = PaperVersion(paper_id=paper_id, **candidate.model_dump())
                    self.store.records.insert('paper_version', version, links=[('paper', paper_id)], conn=conn)
                    versions.append(version)
                source = SourceRecord(paper_version_id=version.id, source=candidate.source,
                                      source_id=candidate.source_id, query=query, metadata=candidate.raw_metadata,
                                      normalized=candidate)
                self.store.records.insert('source_record', source, links=[('version', version.id)], conn=conn)
                associations = self.store.associations(project_id, conn)
                association = next((p for p in associations if p.paper_id == version.paper_id), None)
                if association is None:
                    association = ProjectPaper(project_id=project_id, paper_id=version.paper_id, paper_version_id=version.id)
                    self.store.records.insert('project_paper', association, project_id=project_id,
                                              links=[('paper', version.paper_id), ('version', version.id)], conn=conn)
                result.append(association)
        return result

    def association(self, project_id, paper_id):
        self.projects.get(project_id)
        return self.store.association(project_id, paper_id)

    def versions(self, paper_id):
        self.store.records.get('paper', paper_id, Paper)
        return [v for v in self.store.versions() if v.paper_id == paper_id]

    def available_metadata(self, version_id, *, source_ids=None):
        """Fill missing access metadata without changing immutable paper/source records."""
        version = self.store.records.get('paper_version', version_id, PaperVersion)
        sources = sorted((s for s in self.store.records.list('source_record', SourceRecord)
                          if s.paper_version_id == version_id and (source_ids is None or s.id in source_ids)),
                         key=lambda s: (s.created_at, s.id))
        values, origins = {}, {}
        for name in ('abstract', 'pdf_url'):
            value = getattr(version, name)
            origin = next((s for s in sources if s.normalized and getattr(s.normalized, name) == value), None) if value else None
            if not value:
                origin = next((s for s in sources if s.normalized and getattr(s.normalized, name)), None)
                value = getattr(origin.normalized, name) if origin else None
            values[name] = value
            if origin or sources:
                origins[name] = (origin or sources[0]).id
        return version.model_copy(update=values), origins

    def list_project(self, project_id):
        self.projects.get(project_id)
        documents = EvidenceStore(self.db)
        rows = []
        for paper in self.store.associations(project_id):
            version, origins = self.available_metadata(paper.paper_version_id)
            rows.append(dict(paper.model_dump(mode='json'), version=version.model_dump(mode='json'),
                metadata_sources=origins,
                documents=[document.model_dump(mode='json', exclude={'abstract'})
                           for document in documents.documents(version.id)]))
        return rows

    def decide(self, project_id, paper_id, decision, reason, expected_revision, *, notes=None, paper_version_id=None):
        if decision not in {'pending', 'included', 'excluded'}:
            raise ScholarError(422, 'invalid_decision', 'Invalid literature decision')
        if decision == 'excluded' and not reason.strip():
            raise ScholarError(422, 'reason_required', 'An exclusion reason is required')
        with self.db.transaction() as conn:
            current = self.store.association(project_id, paper_id, conn)
            values = {'human_decision': decision, 'reason': reason}
            if notes is not None:
                values['notes'] = notes
            if paper_version_id is not None:
                version = self.store.records.get('paper_version', paper_version_id, PaperVersion, conn)
                if version.paper_id != paper_id:
                    raise not_found('paper version')
                values['paper_version_id'] = paper_version_id
            return self.store.records.replace('project_paper', current.model_copy(update=values),
                                              expected_revision=expected_revision, conn=conn)

    def freeze_selection(self, project_id, paper_version_ids, expected_revision):
        if not paper_version_ids or len(paper_version_ids) > self.settings.max_included_papers:
            raise ScholarError(422, 'selection_size', f'Select 1–{self.settings.max_included_papers} papers')
        if len(set(paper_version_ids)) != len(paper_version_ids):
            raise ScholarError(422, 'duplicate_selection', 'A paper version may only be selected once')
        with self.db.transaction() as conn:
            self.projects.store.project(project_id, conn)
            revision = self.store.records.next_revision('selection', project_id, conn)
            if expected_revision != revision - 1:
                raise revision_conflict(revision - 1)
            associations = self.store.associations(project_id, conn)
            selected = []
            for version_id in paper_version_ids:
                association = next((p for p in associations if p.paper_version_id == version_id), None)
                if association is None:
                    raise not_found('project paper version')
                if association.human_decision == 'excluded':
                    raise ScholarError(409, 'excluded_paper', 'An excluded paper cannot enter this selection')
                selected.append(association)
            snapshot = SelectionSnapshot(project_id=project_id, revision=revision, paper_version_ids=paper_version_ids)
            self.store.records.insert('selection', snapshot, project_id=project_id,
                                      links=[('version', v) for v in paper_version_ids], conn=conn)
            for association in selected:
                if association.human_decision != 'included':
                    self.store.records.replace('project_paper', association.model_copy(update={'human_decision': 'included'}),
                                               expected_revision=association.revision, conn=conn)
            return snapshot
