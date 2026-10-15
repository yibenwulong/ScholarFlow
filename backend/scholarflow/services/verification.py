import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from ..domain.common import Model
from ..domain.evidence import ClaimStatus
from ..domain.errors import ScholarError
from .model_gateway import ModelRequest


@dataclass(frozen=True)
class QuoteLocation:
    located: bool
    start: int | None = None
    end: int | None = None


def _normalized(text):
    chars, positions = [], []
    index = 0
    while index < len(text):
        # Remove only a line-end hyphen between letters, retaining an offset map.
        match = None
        if text[index] == '-' and index > 0 and text[index - 1].isalpha():
            match = re.match(r'-\r?\n[ \t]*(?=[^\W\d_])', text[index:])
        if match:
            index += len(match.group())
            continue
        char = text[index]
        if char.isspace():
            if chars and chars[-1] == ' ':
                positions[-1] = (positions[-1][0], index + 1)
            else:
                chars.append(' ')
                positions.append((index, index + 1))
        else:
            chars.append(char)
            positions.append((index, index + 1))
        index += 1
    return ''.join(chars), positions


def locate_quote(passage, quote):
    if not quote.strip():
        return QuoteLocation(False)
    haystack, offsets = _normalized(passage.text)
    needle, _ = _normalized(quote)
    needle = needle.strip()
    start = haystack.find(needle)
    if start < 0:
        return QuoteLocation(False)
    return QuoteLocation(True, offsets[start][0], offsets[start + len(needle) - 1][1])


class ClaimCheck(Model):
    status: ClaimStatus
    reason: str
    checker: str = 'source_guard_v1'


class Verifier:
    def __init__(self, gateway=None, *, db=None):
        self.gateway = gateway
        self.db = db

    async def verify_claim(self, claim, evidence, passages):
        by_id = {p.id: p for p in passages}
        located = [e for e in evidence if e.claim_revision_id == claim.id and e.passage_id in by_id
                   and locate_quote(by_id[e.passage_id], e.quote).located]
        if not located:
            return ClaimCheck(status='insufficient_evidence', reason='No supplied quote is located in the source version')
        quotes = '\n'.join(e.quote for e in located)
        numbers = re.findall(r'\d+(?:[.,]\d+)*', claim.text)
        if any(number not in re.findall(r'\d+(?:[.,]\d+)*', quotes) for number in numbers):
            return ClaimCheck(status='insufficient_evidence', reason='Claim contains numbers absent from its quoted evidence')
        if self.db is not None:
            from ..domain.evidence import ClaimRevision
            from ..domain.operations import EvidenceReview
            from ..persistence.records import RecordStore
            records = RecordStore(self.db)
            original = records.get('claim', claim.id, ClaimRevision)
            if original.text == claim.text:
                latest = {}
                for review in records.list('evidence_review', EvidenceReview):
                    if review.evidence_id not in latest or latest[review.evidence_id].revision < review.revision:
                        latest[review.evidence_id] = review
                if all(e.id in latest for e in located):
                    order = ['conflicting', 'insufficient_evidence', 'needs_recheck', 'inferred', 'supported']
                    selected = min((latest[e.id] for e in located), key=lambda r: order.index(r.verdict))
                    return ClaimCheck(status=selected.verdict, reason=selected.reason, checker='human_review_v1')
        if not self.gateway:
            return ClaimCheck(status='needs_recheck', reason='Quote located; semantic support requires model or human review')
        from ..workflows.prompts import VERIFY_PROMPT
        response = await self.gateway.complete(ModelRequest(run_id=claim.run_id, purpose='verify_claim', messages=[
            {'role': 'system', 'content': VERIFY_PROMPT}, {'role': 'user', 'content': json.dumps({
                'claim': claim.text, 'source_scope': claim.source_scope,
                'evidence': [{'quote': e.quote, 'relation': e.relation} for e in located]}, ensure_ascii=False)}]))
        try:
            check = ClaimCheck.model_validate_json(response.text)
        except (ValueError, ValidationError):
            raise ScholarError(422, 'invalid_model_output', 'Verifier returned an invalid structured result') from None
        return check.model_copy(update={'checker': 'model_support_v1'})
