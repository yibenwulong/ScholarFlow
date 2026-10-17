"""Deterministic, lossless quote choices with original passage coordinates."""


def _unit_end(text: str, start: int, max_chars: int) -> int:
    hard_end = min(start + max_chars, len(text))
    if hard_end == len(text):
        return hard_end
    # Prefer readable boundaries in the latter half of the permitted slice.
    # The boundary character stays in the unit; nothing is stripped or rewritten.
    lower = start + max(1, max_chars // 2)
    for end in range(hard_end, lower - 1, -1):
        last = text[end - 1]
        if last in '。！？' or (last in '.!?' and text[end].isspace()):
            return end
    for end in range(hard_end, lower - 1, -1):
        if text[end - 1].isspace():
            return end
    return hard_end


def quote_units(batch: list[dict], *, max_chars=400) -> list[dict]:
    """Return exact source slices, numbered across this batch starting at Q0001.

    ``start`` and ``end`` use Python character offsets in the original Passage,
    rather than offsets in the fragment, encoded bytes, or normalized text.
    Empty fragments produce no choices and retain later fragment indices.
    """
    if max_chars < 1:
        raise ValueError('max_chars must be positive')
    result = []
    for fragment_index, fragment in enumerate(batch):
        text = fragment['text']
        start = 0
        while start < len(text):
            end = _unit_end(text, start, max_chars)
            result.append({
                'quote_id': f'Q{len(result) + 1:04d}',
                'fragment_index': fragment_index,
                'passage_id': fragment['passage_id'],
                'document_version_id': fragment['document_version_id'],
                'source_scope': fragment['source_scope'],
                'page_number': fragment['page_number'],
                'start': fragment['start'] + start,
                'end': fragment['start'] + end,
                'text': text[start:end],
            })
            start = end
    return result
