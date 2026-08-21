"""Contracts: versioned prompt + typed output + autofix/hard-fail guards."""

from mastervault.contracts.base import Contract, DispatchResult, schema_section
from mastervault.contracts.claims import (
    ClaimCandidate,
    ClaimExtractionContract,
    ClaimExtractionOut,
)
from mastervault.contracts.contradiction import (
    ContradictionJudgeContract,
    ContradictionVerdictOut,
)
from mastervault.contracts.corpus_check import CorpusCheckContract, CorpusCheckOut
from mastervault.contracts.generic_grounded_claims import (
    GenericGroundedClaimCandidateV2,
    GenericGroundedClaimExtractionContractV2,
    GenericGroundedClaimExtractionV2,
)
from mastervault.contracts.judge import SufficiencyJudgeContract, SufficiencyVerdictOut
from mastervault.contracts.page_grounded_claims import (
    EvidenceCandidate,
    PageGroundedClaimCandidate,
    PageGroundedClaimExtractionContract,
    PageGroundedClaimExtractionOut,
)
from mastervault.contracts.synthesis import GroundedAnswerOut, GroundedSynthesisContract
from mastervault.contracts.wiki_draft import WikiDraftContract, WikiDraftOut

__all__ = [
    "ClaimCandidate",
    "ClaimExtractionContract",
    "ClaimExtractionOut",
    "Contract",
    "ContradictionJudgeContract",
    "ContradictionVerdictOut",
    "CorpusCheckContract",
    "CorpusCheckOut",
    "DispatchResult",
    "GroundedAnswerOut",
    "GroundedSynthesisContract",
    "GenericGroundedClaimCandidateV2",
    "GenericGroundedClaimExtractionContractV2",
    "GenericGroundedClaimExtractionV2",
    "EvidenceCandidate",
    "PageGroundedClaimCandidate",
    "PageGroundedClaimExtractionContract",
    "PageGroundedClaimExtractionOut",
    "SufficiencyJudgeContract",
    "SufficiencyVerdictOut",
    "WikiDraftContract",
    "WikiDraftOut",
    "schema_section",
]
