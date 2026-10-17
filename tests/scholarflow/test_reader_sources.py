"""Quote choices must remain exact contiguous slices of their original sources."""
from copy import deepcopy

import pytest


def units(batch, **options):
    # Import at call time so the initial RED is a missing feature, not collection failure.
    from backend.scholarflow.services.reader_sources import quote_units
    return quote_units(batch, **options)


def fragment(text, *, start=0, passage_id='passage-a', document_id='document-a', scope='fulltext', page=3):
    return {'passage_id': passage_id, 'document_version_id': document_id, 'source_scope': scope,
            'page_number': page, 'start': start, 'end': start + len(text), 'text': text}


def test_empty_batch_has_no_quote_choices():
    assert units([]) == []


@pytest.mark.parametrize('limit', [0, -1, -400])
def test_nonpositive_unit_limit_is_rejected_even_for_empty_input(limit):
    with pytest.raises(ValueError):
        units([], max_chars=limit)


def test_ids_and_source_coordinates_are_global_in_batch_but_never_merge_fragments():
    batch = [fragment('abcdefg', start=11),
             fragment('abc', start=40, passage_id='passage-b', document_id='document-b', scope='abstract', page=None)]
    assert units(batch, max_chars=3) == [
        {'quote_id': 'Q0001', 'fragment_index': 0, 'passage_id': 'passage-a', 'document_version_id': 'document-a',
         'source_scope': 'fulltext', 'page_number': 3, 'start': 11, 'end': 14, 'text': 'abc'},
        {'quote_id': 'Q0002', 'fragment_index': 0, 'passage_id': 'passage-a', 'document_version_id': 'document-a',
         'source_scope': 'fulltext', 'page_number': 3, 'start': 14, 'end': 17, 'text': 'def'},
        {'quote_id': 'Q0003', 'fragment_index': 0, 'passage_id': 'passage-a', 'document_version_id': 'document-a',
         'source_scope': 'fulltext', 'page_number': 3, 'start': 17, 'end': 18, 'text': 'g'},
        {'quote_id': 'Q0004', 'fragment_index': 1, 'passage_id': 'passage-b', 'document_version_id': 'document-b',
         'source_scope': 'abstract', 'page_number': None, 'start': 40, 'end': 43, 'text': 'abc'},
    ]


@pytest.mark.parametrize(('text', 'limit'), [
    ('研究未发现改善。准确率为９５．２％，不能称为显著。\n下一行', 8),
    ('Cafe\u0301 ﬁ ① 𝒜 µ μ Ａ—B\u00a0不等于 A-B。\n', 5),
    ('A hyphen-\nated method did not\n improve F1 by 0.25.\r\n  End  ', 9),
    ('same same same\nsame same same', 7),
    (' \t\n\r\n  ', 2),
    ('👩\u200d🔬🧪e\u0301否定❌' * 4, 3),
    ('x' * 1003, 400),
    ('不', 1),
])
def test_units_preserve_every_character_and_map_to_original_passage(text, limit):
    passage = '前' * 37 + text + '尾巴'
    original = fragment(text, start=37)
    result = units([original], max_chars=limit)
    assert ''.join(item['text'] for item in result) == text
    cursor = 37
    for item in result:
        assert 1 <= len(item['text']) <= limit
        assert item['fragment_index'] == 0
        assert item['start'] == cursor
        assert item['end'] == item['start'] + len(item['text'])
        assert passage[item['start']:item['end']] == item['text']
        cursor = item['end']
    assert cursor == original['end']


def test_default_limit_splits_a_long_unbroken_token_without_dropping_its_tail():
    result = units([fragment('x' * 1003)])
    assert [len(item['text']) for item in result] == [400, 400, 203]
    assert [item['quote_id'] for item in result] == ['Q0001', 'Q0002', 'Q0003']
    assert [item['start'] for item in result] == [0, 400, 800]
    assert [item['end'] for item in result] == [400, 800, 1003]


def test_empty_fragments_keep_later_fragment_indices_without_consuming_quote_ids():
    batch = [fragment(''), fragment('否定 7.3', start=9, passage_id='p2'), fragment('')]
    result = units(batch)
    assert len(result) == 1
    assert result[0]['quote_id'] == 'Q0001'
    assert result[0]['fragment_index'] == 1
    assert result[0]['text'] == '否定 7.3'
    assert (result[0]['start'], result[0]['end']) == (9, 15)


def test_repeated_text_in_different_sources_keeps_each_sources_identity():
    batch = [fragment('相同的文本', start=4),
             fragment('相同的文本', start=21, passage_id='p2', document_id='d2', scope='abstract', page=None),
             fragment('相同的文本', start=30, passage_id='p2', document_id='d2', scope='abstract', page=None)]
    result = units(batch)
    assert [item['quote_id'] for item in result] == ['Q0001', 'Q0002', 'Q0003']
    assert [item['fragment_index'] for item in result] == [0, 1, 2]
    assert [(item['document_version_id'], item['passage_id'], item['start'], item['end']) for item in result] == [
        ('document-a', 'passage-a', 4, 9), ('d2', 'p2', 21, 26), ('d2', 'p2', 30, 35)]
    assert [item['source_scope'] for item in result] == ['fulltext', 'abstract', 'abstract']
    assert [item['page_number'] for item in result] == [3, None, None]


def test_result_is_deterministic_and_does_not_mutate_input():
    batch = [fragment('  First sentence. Second sentence without an end\nNot 0.75 or １．２。  ', start=12),
             fragment('重复重复重复重复重复', start=4, document_id='d2')]
    before = deepcopy(batch)
    first = units(batch, max_chars=17)
    second = units(batch, max_chars=17)
    assert first == second
    assert batch == before
    assert len({item['quote_id'] for item in first}) == len(first)
    for index, source in enumerate(batch):
        selected = [item for item in first if item['fragment_index'] == index]
        assert ''.join(item['text'] for item in selected) == source['text']
