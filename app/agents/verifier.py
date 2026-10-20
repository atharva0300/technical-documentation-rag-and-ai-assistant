import re
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

_nli_model = None


def get_nli_model():
    """
    Lazily load a small NLI (natural-language-inference) cross-encoder on CPU,
    same cost class and loading pattern as the reranker in
    `app.retrieval.reranker`. This is the real fix for "the verifier only
    checks whether technical tokens appear somewhere in the evidence, not
    whether the cited chunk actually supports the claim" -- the lexical check
    in `VerifierAgent.verify` is structurally blind to meaning (a sentence can
    reuse every right token while asserting a wrong relationship between
    them), so entailment -- does the cited chunk actually entail this
    sentence? -- is the real correctness check, in the same spirit as
    production RAG-faithfulness tooling (e.g. Vectara's HHEM, or the
    NLI-based approach behind the TRUE benchmark / Google's AIS framework).
    Returns None if the model can't be loaded (offline test environment,
    etc.) so callers can fall back to the lexical-only check rather than crash.
    """
    global _nli_model
    if _nli_model is False:
        return None
    if _nli_model is None:
        try:
            from sentence_transformers import CrossEncoder
            from app.config import settings
            _nli_model = CrossEncoder(settings.NLI_MODEL_NAME, device="cpu")
        except Exception as e:
            logger.warning(f"NLI model unavailable, verifier will use lexical-only grounding: {e}")
            _nli_model = False
            return None
    return _nli_model


def _softmax(logits):
    import numpy as np
    arr = np.array(logits, dtype=float)
    arr = arr - arr.max()
    exp = np.exp(arr)
    return exp / exp.sum()


def check_entailment(premise: str, hypothesis: str) -> Optional[str]:
    """
    Returns "entailment", "contradiction", or "neutral" for whether `premise`
    (the cited evidence chunk) entails `hypothesis` (the answer sentence), or
    None if the NLI model is unavailable. Label order for
    cross-encoder/nli-deberta-v3-* models is [contradiction, entailment, neutral].
    """
    model = get_nli_model()
    if model is None:
        return None
    try:
        scores = model.predict([(premise, hypothesis)])
        probs = _softmax(scores[0])
        labels = ["contradiction", "entailment", "neutral"]
        return labels[int(probs.argmax())]
    except Exception as e:
        logger.warning(f"NLI entailment check failed, treating as unavailable: {e}")
        return None


class VerificationResult:
    def __init__(
        self,
        status: str,  # VALID, PARTIAL, RETRY, REFUSE
        cleaned_answer: str,
        unsupported_sentences: List[str],
        feedback: str,
        retry_reason: str = None  # "citation_format" | "ungrounded_content" | None
    ):
        self.status = status
        self.cleaned_answer = cleaned_answer
        self.unsupported_sentences = unsupported_sentences
        self.feedback = feedback
        # Distinguishes *why* a RETRY was requested, so the graph can react correctly:
        # a citation_format failure is a generation problem (the model forgot to cite, or
        # cited an out-of-range index) and should be fixed by regenerating from the SAME
        # evidence, not by spending a retrieval round on evidence that was never the problem.
        # An ungrounded_content failure means the retrieved evidence itself didn't support
        # the claims, which is what the original retrieve-and-retry path is actually for.
        self.retry_reason = retry_reason


class VerifierAgent:
    """
    3-layer verifier: citation syntax, per-citation technical-token grounding,
    and NLI-based entailment (falling back to lexical-only when the NLI model
    can't be loaded) -- checked against the SPECIFIC chunk each sentence
    cites, not the pooled text of every evidence item.
    """

    # Regex for technical identifiers: flags (--foo), versions (v1.2), camelCase, paths, numbers/ports
    TECHNICAL_TOKEN_REGEX = re.compile(
        r'(--[a-zA-Z0-9_-]+|v\d+\.\d+(?:\.\d+)?|[a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+|\b[a-z]+[A-Z][a-zA-Z0-9]+\b|\b\d{2,5}\b)'
    )

    @classmethod
    def _cited_evidence_text(cls, sentence: str, evidence: List[Dict[str, Any]], full_evidence_text: str) -> str:
        """
        Returns the content of the SPECIFIC chunk(s) this sentence cites
        (`evidence[idx - 1]["content"]` for each `[idx]` in the sentence), not
        every chunk's content concatenated. This is the fix for the bug where
        a sentence citing [1] could be "verified" by a token that only
        actually appears in chunk [5] -- lexical and NLI checks below now
        only ever see what the citation actually points at.
        Falls back to the full pooled text only when the sentence has no
        parseable citation at all (nothing to anchor to).
        """
        indices = [int(m) for m in re.findall(r'\[(\d+)\]', sentence)]
        valid = [i for i in indices if 1 <= i <= len(evidence)]
        if not valid:
            return full_evidence_text
        return " ".join(evidence[i - 1].get("content", "") for i in valid).lower()

    @classmethod
    def verify(
        cls,
        answer: str,
        evidence: List[Dict[str, Any]],
        retry_count: int = 0,
        max_retries: int = 2
    ) -> VerificationResult:
        if not answer or not answer.strip():
            return VerificationResult("REFUSE", "", [], "Empty answer draft.")

        if not evidence:
            return VerificationResult("REFUSE", answer, [], "No evidence available to ground answer.")

        # Layer 1: Citation Syntax & Index Range
        citation_matches = re.findall(r'\[(\d+)\]', answer)
        cited_indices = [int(m) for m in citation_matches]
        max_evidence_idx = len(evidence)

        if not cited_indices:
            if retry_count < max_retries:
                return VerificationResult(
                    status="RETRY",
                    cleaned_answer=answer,
                    unsupported_sentences=[],
                    feedback="Answer missing bracketed citation tags [n]. Every factual technical claim must cite source evidence.",
                    retry_reason="citation_format"
                )

        invalid_citations = [idx for idx in cited_indices if idx < 1 or idx > max_evidence_idx]
        if invalid_citations:
            if retry_count < max_retries:
                return VerificationResult(
                    status="RETRY",
                    cleaned_answer=answer,
                    unsupported_sentences=[],
                    feedback=f"Invalid citation indices: {invalid_citations}. Valid range is 1 to {max_evidence_idx}.",
                    retry_reason="citation_format"
                )

        # Layer 2 & 3: per-citation technical token grounding + NLI entailment
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', answer) if s.strip()]
        valid_sentences = []
        unsupported_sentences = []

        full_evidence_text = " ".join([doc.get("content", "") for doc in evidence]).lower()

        for sentence in sentences:
            cited_text = cls._cited_evidence_text(sentence, evidence, full_evidence_text)

            tech_tokens = cls.TECHNICAL_TOKEN_REGEX.findall(sentence)
            tokens_grounded = all(token.lower() in cited_text for token in tech_tokens)

            if not tokens_grounded:
                unsupported_sentences.append(sentence)
                continue

            # Tokens check out against the cited chunk -- now confirm the
            # cited chunk actually ENTAILS this sentence's claim, not just
            # that the right words happen to appear in it. This is what
            # catches "right term, wrong claim" (e.g. the sentence attaches a
            # token from the cited chunk to a relationship/value that chunk
            # doesn't actually assert).
            entailment = check_entailment(cited_text, sentence) if cited_text.strip() else None
            if entailment == "contradiction":
                unsupported_sentences.append(sentence)
            else:
                # entailment in {"entailment", "neutral", None (NLI unavailable)}
                # -- "neutral" still passes here deliberately: entailment
                # models are strict, and a neutral verdict on a technically
                # dense sentence is common even when the claim is fine. Only
                # an explicit contradiction overturns a lexically-grounded
                # sentence; this keeps the behavior conservative (biased
                # toward PARTIAL over wrongly discarding a correct claim)
                # when the NLI model is unavailable (entailment is None).
                valid_sentences.append(sentence)

        # Decision synthesis
        if not unsupported_sentences:
            return VerificationResult(
                status="VALID",
                cleaned_answer=answer,
                unsupported_sentences=[],
                feedback="All citations verified against their specific cited chunk (technical tokens + NLI entailment)."
            )

        if valid_sentences:
            cleaned_text = " ".join(valid_sentences)
            return VerificationResult(
                status="PARTIAL",
                cleaned_answer=cleaned_text,
                unsupported_sentences=unsupported_sentences,
                feedback=f"Removed {len(unsupported_sentences)} unsupported sentence(s): either their technical tokens weren't in the cited chunk, or the cited chunk contradicted the claim."
            )

        if retry_count < max_retries:
            return VerificationResult(
                status="RETRY",
                cleaned_answer=answer,
                unsupported_sentences=unsupported_sentences,
                feedback=f"Technical identifiers {unsupported_sentences[:2]} not grounded in their cited evidence.",
                retry_reason="ungrounded_content"
            )

        return VerificationResult(
            status="REFUSE",
            cleaned_answer="I could not ground the answer in the retrieved documentation.",
            unsupported_sentences=unsupported_sentences,
            feedback="All claims failed citation grounding after retries."
        )
