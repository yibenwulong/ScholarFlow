import pytest

from backend.scholarflow.domain.evidence import ClaimRevision, Evidence, Passage
from backend.scholarflow.services.verification import Verifier, locate_quote


def test_quote_normalization_preserves_offsets_digits_and_negation():
    text = 'The multi-\nagent model did not improve: 84.2 percent.'
    passage = Passage(document_version_id='doc', text=text, end=len(text))
    location = locate_quote(passage, 'multiagent model did not improve: 84.2 percent')
    assert location.located
    assert text[location.start:location.end] == 'multi-\nagent model did not improve: 84.2 percent'
    assert not locate_quote(passage, 'model did improve: 84.2 percent').located
    assert not locate_quote(passage, 'model did not improve: 94.2 percent').located


@pytest.mark.asyncio
async def test_wrong_number_and_fabricated_quote_cannot_be_supported():
    passage = Passage(document_version_id='doc', text='Accuracy was 84 percent.', end=24)
    claim = ClaimRevision(run_id='run', text='Accuracy was 94 percent.', source_scope='abstract')
    evidence = Evidence(claim_revision_id=claim.id, passage_id=passage.id, quote=passage.text)
    check = await Verifier().verify_claim(claim, [evidence], [passage])
    assert check.status == 'insufficient_evidence'
    assert check.checker == 'source_guard_v1'
    evidence = evidence.model_copy(update={'quote': 'Accuracy was 94 percent.'})
    assert (await Verifier().verify_claim(claim, [evidence], [passage])).status == 'insufficient_evidence'
