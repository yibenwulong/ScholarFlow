"""Explicit, self-authored synthetic fixtures. Never used as an API-key fallback."""
import json

import pymupdf

from .domain.library import PaperCandidate
from .services.model_gateway import ModelResponse
from .services.providers.base import SearchResult

SAMPLES = {'graph-a': ('Synthetic Graph Collaboration A', 84),
           'graph-b': ('Synthetic Graph Collaboration B', 87),
           'abstract-c': ('Synthetic Abstract-only Baseline', 72)}


class DemoProvider:
    def __init__(self, name): self.name = name
    async def search(self, query, limit=20):
        return SearchResult(source=self.name, query=query, candidates=[PaperCandidate(title=title,
            authors=['ScholarFlow Synthetic Examples'], year=2025, source='demo', source_id=key,
            identifiers={'demo': key}, abstract='The method uses a graph. Synthetic demonstration only.',
            publication_status='synthetic_fixture', raw_metadata={'scholarflow_demo': key})
            for key, (title, _) in list(SAMPLES.items())[:limit]])


def make_demo_pdf(key):
    if key not in SAMPLES or key == 'abstract-c':
        return None
    title, score = SAMPLES[key]
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 65), title + '\nSelf-authored synthetic fixture. Not a real scientific publication.')
        document.new_page().insert_text((50, 65),
            f'The method uses a graph.\nAccuracy was {score} percent on synthetic examples.\nLimitations: synthetic data only.')
        return document.tobytes(no_new_id=True)


class DemoModel:
    async def complete(self, request):
        inputs = json.loads(request.messages[-1]['content'])
        if request.purpose == 'plan':
            output = {'queries': ['synthetic multi-agent graph collaboration'], 'subquestions': ['Compare the synthetic methods and limitations'],
                      'inclusion_criteria': ['Explicit demonstration fixtures'], 'exclusion_criteria': ['Unrelated material']}
        elif request.purpose == 'screen':
            output = {'recommendations': [{'paper_version_id': paper['id'], 'decision': 'included',
                'reason': 'Synthetic fixture relevant to the demonstration; human confirmation required'} for paper in inputs['papers']]}
        elif request.purpose == 'read_paper':
            passage = next((p for p in inputs['passages'] if 'The method uses a graph.' in p['text']), None)
            fields = {name: [] for name in ('research_question', 'method', 'dataset', 'findings', 'limitations')}
            if passage:
                fields['method'] = [{'value': 'The method uses a graph.', 'quote': 'The method uses a graph.',
                                     'passage_id': passage['passage_id']}]
            output = {'fields': fields, 'missing_reasons': {name: 'Not stated in this synthetic fixture fragment.'
                                                           for name, values in fields.items() if not values}}
        elif request.purpose == 'write_report':
            blocks = []
            for index, entry in enumerate(inputs['extractions']):
                field = entry['fields']['method']
                if not field['value']:
                    continue
                text = field['value']
                blocks.append({'id': 'method-' + str(index), 'heading': '方法比较 · 合成文献 ' + str(index + 1),
                    'text': text + f" [@{entry['key']}]", 'claims': [{'start': 0, 'end': len(text), 'token_index': 0,
                        'claim_revision_ids': field['claim_ids'], 'evidence_ids': field['evidence_ids']}]})
            output = {'title': 'ScholarFlow 合成文献演示报告', 'blocks': blocks}
        elif request.purpose == 'verify_claim':
            output = {'status': 'supported' if inputs['claim'].strip() == 'The method uses a graph.' else 'needs_recheck',
                      'reason': 'Deterministic synthetic fixture assessment; not an evaluation of real scientific findings'}
        else:
            raise ValueError('Unknown demo model operation')
        return ModelResponse(text=json.dumps(output, ensure_ascii=False))
