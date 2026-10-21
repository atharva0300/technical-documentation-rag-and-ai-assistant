from typing import List, Dict, Any, Optional
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError

ANSWERER_SYSTEM_PROMPT = """You are an expert software engineer who explains technical documentation clearly.
Answer the user's question using ONLY the numbered evidence excerpts you are given.

How to write the answer:
- Start with a direct answer in one or two sentences, then explain the details that matter.
- Write natural, well-organized explanations in your own words, the way a knowledgeable
  colleague would. Do not copy the excerpts verbatim and do not walk through the sources one
  by one ("Source [1] says..., Source [2] says...").
- Put a citation such as [2] right after each sentence that uses information from that
  excerpt; use [1][3] when a sentence draws on several. Cite only excerpts that actually
  support the sentence. Do not add a list of sources at the end.
- Use Markdown where it helps: short paragraphs, bullet points for steps or options, and
  fenced code blocks (```java, ```xml, ...) for code or configuration. If the user asks for
  code, include an example taken from the evidence.
- Keep class names, annotations, XML elements, property names and version numbers exactly as
  they appear in the evidence.
- Ignore excerpts that are not relevant to the question.
- If the evidence does not answer the question, say so plainly and mention what the excerpts
  do cover. Never make up information that is not in the evidence.
"""

FALLBACK_INTRO = (
    "The language model is unavailable right now, so here are the most relevant "
    "excerpts from the documentation:"
)


def citation_label(doc: Dict[str, Any]) -> str:
    """Readable name for a source: its section (e.g. "7.14 Registering a LoadTimeWeaver ·
    p. 170") plus the document title, or just the title when there's no section."""
    title = doc.get("title") or doc.get("source") or "Document"
    section = doc.get("section") or doc.get("section_path") or ""
    return f"{section} — {title}" if section else title


class AnswererAgent:
    """
    Synthesizes grounded Markdown answers from merged evidence chunks.
    """

    @staticmethod
    def format_evidence_prompt(query: str, evidence: List[Dict[str, Any]]) -> str:
        formatted_blocks = []
        for doc in evidence:
            cid = doc.get("citation_id", 1)
            content = doc.get("content", "").strip()

            formatted_blocks.append(
                f"[{cid}] Source: {citation_label(doc)}\nContent:\n{content}"
            )

        evidence_text = "\n\n---\n\n".join(formatted_blocks)

        return (
            f"Evidence excerpts:\n\n{evidence_text}\n\n"
            f"Question: {query}\n\n"
            "Answer the question in clear, natural prose, citing excerpts inline with [n] "
            "right after the sentences they support."
        )

    @classmethod
    def generate_answer(cls, query: str, evidence: List[Dict[str, Any]]) -> str:
        if not evidence:
            return "I could not find enough relevant information in the technical documentation to answer your request."
            
        prompt = cls.format_evidence_prompt(query, evidence)
        
        try:
            return llm_gateway.generate(prompt=prompt, system=ANSWERER_SYSTEM_PROMPT)
        except OllamaUnavailableError:
            # Deterministic fallback when Ollama is offline or in test replay mode
            top_evidence = evidence[:3]
            lines = [FALLBACK_INTRO]
            for doc in top_evidence:
                cid = doc.get("citation_id", 1)
                content = doc.get("content", "").strip().replace("\n", " ")
                lines.append(f"[{cid}] **{citation_label(doc)}**: {content[:300]}...")
            return "\n\n".join(lines)

    @classmethod
    async def generate_answer_stream(cls, query: str, evidence: List[Dict[str, Any]]):
        if not evidence:
            yield "I could not find enough relevant information in the technical documentation to answer your request."
            return

        prompt = cls.format_evidence_prompt(query, evidence)
        try:
            async for token in llm_gateway.astream(prompt=prompt, system=ANSWERER_SYSTEM_PROMPT):
                yield token
        except OllamaUnavailableError:
            top_evidence = evidence[:3]
            lines = [FALLBACK_INTRO]
            for doc in top_evidence:
                cid = doc.get("citation_id", 1)
                content = doc.get("content", "").strip().replace("\n", " ")
                lines.append(f"[{cid}] **{citation_label(doc)}**: {content[:300]}...")
            fallback = "\n\n".join(lines)
            for chunk in fallback.split(" "):
                yield chunk + " "
