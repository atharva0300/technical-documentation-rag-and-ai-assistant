from typing import List, Dict, Any, Optional
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError

ANSWERER_SYSTEM_PROMPT = """You are a technical documentation AI assistant. 
Your goal is to answer the user's technical question based STRICTLY on the provided evidence chunks.

Guidelines:
1. Base every factual statement on the provided evidence chunks.
2. Cite your sources using inline numeric tags like [1], [2] matching the evidence item numbers.
3. Keep code syntax, flags, version numbers, and CLI commands exact.
4. If the evidence is insufficient to answer the query, clearly state what information is missing.
5. Provide a direct, concise, technical explanation.
"""

class AnswererAgent:
    """
    Synthesizes grounded Markdown answers from merged evidence chunks.
    """

    @staticmethod
    def format_evidence_prompt(query: str, evidence: List[Dict[str, Any]]) -> str:
        formatted_blocks = []
        for doc in evidence:
            cid = doc.get("citation_id", 1)
            title = doc.get("title") or doc.get("source", "Document")
            section = doc.get("section") or doc.get("section_path") or ""
            sec_str = f" > {section}" if section else ""
            content = doc.get("content", "").strip()
            
            formatted_blocks.append(
                f"[{cid}] Source: {title}{sec_str}\nContent:\n{content}"
            )
            
        evidence_text = "\n\n---\n\n".join(formatted_blocks)
        
        return f"User Question: {query}\n\nRetrieved Evidence:\n{evidence_text}\n\nPlease generate a grounded Markdown answer citing the evidence using inline [n] tags."

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
            lines = [f"Based on the indexed technical documentation for '{query}':\n"]
            for doc in top_evidence:
                cid = doc.get("citation_id", 1)
                title = doc.get("title") or doc.get("source", "doc")
                content = doc.get("content", "").strip().replace("\n", " ")
                lines.append(f"[{cid}] **{title}**: {content[:300]}...")
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
            lines = [f"Based on the indexed technical documentation for '{query}':\n"]
            for doc in top_evidence:
                cid = doc.get("citation_id", 1)
                title = doc.get("title") or doc.get("source", "doc")
                content = doc.get("content", "").strip().replace("\n", " ")
                lines.append(f"[{cid}] **{title}**: {content[:300]}...")
            fallback = "\n\n".join(lines)
            for chunk in fallback.split(" "):
                yield chunk + " "
