from dataclasses import dataclass
import re

from ..domain.errors import ScholarError

CITATION = re.compile(r'\[@([^\]\s]+)\]')


@dataclass(frozen=True)
class RenderedReport:
    markdown: str
    references: list[dict]


def citation_tokens(text):
    return list(CITATION.finditer(text))


def render_citations(version):
    bindings = {b.key: b for b in version.bindings}
    numbers, references, sections = {}, [], ['# ' + version.title]
    def replace(match):
        key = match.group(1)
        if key not in bindings:
            raise ScholarError(422, 'unknown_citation_key', f'Unknown citation key: {key}')
        if key not in numbers:
            numbers[key] = len(numbers) + 1
            references.append({'number': numbers[key], 'key': key, 'paper_version_id': bindings[key].paper_version_id,
                               **bindings[key].bibliography_snapshot})
        return f'[{numbers[key]}]'
    for block in version.blocks:
        if block.heading:
            sections.append('## ' + block.heading)
        sections.append(CITATION.sub(replace, block.text))
    sections.append('## References')
    for ref in references:
        authors = ', '.join(ref.get('authors') or []) or 'Authors unreported'
        year = ref.get('year') or 'Year unreported'
        sections.append(f"[{ref['number']}] {authors}. {ref.get('title', 'Title unreported')}. {year}.")
    return RenderedReport('\n\n'.join(sections), references)
