from typing import TypedDict


class ResearchState(TypedDict):
    run_id: str
    generation: int
    stage: str
