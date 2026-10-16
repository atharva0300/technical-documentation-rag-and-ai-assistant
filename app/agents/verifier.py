import re
from typing import List, Dict, Any, Tuple

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
    3-layer deterministic verifier checking syntax, technical token grounding, and claim support.
    """

    # Regex for technical identifiers: flags (--foo), versions (v1.2), camelCase, paths, numbers/ports
    TECHNICAL_TOKEN_REGEX = re.compile(
        r'(--[a-zA-Z0-9_-]+|v\d+\.\d+(?:\.\d+)?|[a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+|\b[a-z]+[A-Z][a-zA-Z0-9]+\b|\b\d{2,5}\b)'
    )

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

        # Layer 2 & 3: Sentence-level technical token grounding & claim support
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', answer) if s.strip()]
        valid_sentences = []
        unsupported_sentences = []

        # Combine all evidence content for token lookup
        full_evidence_text = " ".join([doc.get("content", "") for doc in evidence]).lower()

        for sentence in sentences:
            # Extract technical tokens in this sentence
            tech_tokens = cls.TECHNICAL_TOKEN_REGEX.findall(sentence)
            tokens_grounded = True
            
            for token in tech_tokens:
                if token.lower() not in full_evidence_text:
                    tokens_grounded = False
                    break

            if not tokens_grounded:
                unsupported_sentences.append(sentence)
            else:
                valid_sentences.append(sentence)

        # Decision synthesis
        if not unsupported_sentences:
            return VerificationResult(
                status="VALID",
                cleaned_answer=answer,
                unsupported_sentences=[],
                feedback="All citations and technical tokens verified successfully."
            )

        if valid_sentences:
            cleaned_text = " ".join(valid_sentences)
            return VerificationResult(
                status="PARTIAL",
                cleaned_answer=cleaned_text,
                unsupported_sentences=unsupported_sentences,
                feedback=f"Removed {len(unsupported_sentences)} unsupported sentence(s) with unverified technical tokens."
            )

        if retry_count < max_retries:
            return VerificationResult(
                status="RETRY",
                cleaned_answer=answer,
                unsupported_sentences=unsupported_sentences,
                feedback=f"Technical identifiers {unsupported_sentences[:2]} not found in retrieved evidence.",
                retry_reason="ungrounded_content"
            )

        return VerificationResult(
            status="REFUSE",
            cleaned_answer="I could not ground the answer in the retrieved documentation.",
            unsupported_sentences=unsupported_sentences,
            feedback="All claims failed technical token grounding after retries."
        )
