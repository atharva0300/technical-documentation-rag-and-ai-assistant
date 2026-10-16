from typing import List, Literal, Optional, Callable
from pydantic import BaseModel, Field
import json
import re
import logging
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError

logger = logging.getLogger(__name__)

class PlanOutput(BaseModel):
    intent: Literal["single_lookup", "multi_hop", "comparison", "exact_term", "chitchat", "out_of_scope"] = "single_lookup"
    needs_retrieval: bool = True
    needs_tools: bool = False
    selected_tool: Optional[str] = None
    tool_args: Optional[dict] = None
    sub_questions: List[str] = Field(default_factory=list)
    reasoning: Optional[str] = None

class PlannerAgent:
    """
    Structured planner for query intent classification and capability tool selection.
    Combines deterministic fast-paths with LLM-backed classification for ambiguous queries.
    """
    
    # search_documentation is the only MCP tool the server exposes (see mcp_server/server.py);
    # earlier versions allow-listed 6 more tools here that no planner branch could ever select
    # (get_document*) or that were permanently disabled without a GITHUB_TOKEN nobody configured
    # (search_code/get_file/search_issues) — removed rather than kept as unreachable surface area.
    ALLOWED_TOOLS = {"search_documentation"}

    @classmethod
    def plan(
        cls,
        query: str,
        history: Optional[List[dict]] = None,
        llm_fn: Optional[Callable[[str, str], str]] = None
    ) -> PlanOutput:
        clean_query = query.strip()
        lower_q = clean_query.lower()
        
        # 1. Deterministic Fast-Paths for Chitchat
        if lower_q in ["hi", "hello", "hey", "good morning", "good evening", "who are you"]:
            return PlanOutput(
                intent="chitchat",
                needs_retrieval=False,
                needs_tools=False,
                sub_questions=[],
                reasoning="Deterministic fast-path for conversational greeting."
            )
            
        # 2. Deterministic Fast-Paths for Out-Of-Scope queries
        out_of_scope_keywords = ["poem", "joke", "recipe", "song", "movie", "weather", "sports", "football"]
        if any(keyword in lower_q for keyword in out_of_scope_keywords):
            return PlanOutput(
                intent="out_of_scope",
                needs_retrieval=False,
                needs_tools=False,
                sub_questions=[],
                reasoning="Deterministic fast-path for out of scope query."
            )

        # 3. Exact term query (CLI flags like --memory, error codes, kubectl/docker commands)
        if "--" in clean_query or "error:" in lower_q or lower_q.startswith("kubectl ") or lower_q.startswith("docker "):
            return PlanOutput(
                intent="exact_term",
                needs_retrieval=True,
                needs_tools=False,
                selected_tool="search_documentation",
                tool_args={"query": clean_query},
                sub_questions=[clean_query],
                reasoning="Detected technical flag/CLI exact term query."
            )

        # 4. Unambiguous Comparison Intent (explicit vs, vs., compare X and Y, difference between)
        if re.search(r"\bvs\.?\b", lower_q) or "compare " in lower_q or "difference between " in lower_q:
            parts = re.split(r"\bvs\.?\b|\bcompare\b|\bdifference between\b", clean_query, flags=re.IGNORECASE)
            sub_q = [p.strip() for p in parts if len(p.strip()) > 3]
            if len(sub_q) >= 2:
                return PlanOutput(
                    intent="comparison",
                    needs_retrieval=True,
                    needs_tools=False,
                    sub_questions=sub_q[:2],
                    reasoning="Detected unambiguous comparison query."
                )

        # 5. Unambiguous Multi-hop Intent (explicit "and how does", "explain both")
        if "and how does" in lower_q or "explain both" in lower_q:
            sub_q = [q.strip() for q in clean_query.split("and") if len(q.strip()) > 5]
            if len(sub_q) >= 2:
                return PlanOutput(
                    intent="multi_hop",
                    needs_retrieval=True,
                    needs_tools=False,
                    sub_questions=sub_q[:3],
                    reasoning="Detected unambiguous multi-hop query."
                )

        # 6. LLM Fallback Path for Ambiguous Queries
        system_prompt = (
            "You are a query classification and planning assistant for a technical RAG system.\n"
            "Analyze the user query and output a JSON object matching this schema:\n"
            "{\n"
            '  "intent": "single_lookup" | "multi_hop" | "comparison" | "exact_term" | "chitchat" | "out_of_scope",\n'
            '  "needs_retrieval": boolean,\n'
            '  "sub_questions": [string],\n'
            '  "reasoning": string\n'
            "}\n"
            "Do not include markdown codeblocks or extra prose. Output pure JSON only."
        )

        try:
            if llm_fn is not None:
                llm_response = llm_fn(clean_query, system_prompt)
            else:
                llm_response = llm_gateway.generate(clean_query, system=system_prompt)

            cleaned_json = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", llm_response, flags=re.DOTALL).strip()
            data = json.loads(cleaned_json)
            
            tool = data.get("selected_tool")
            if tool and tool not in cls.ALLOWED_TOOLS:
                tool = None

            return PlanOutput(
                intent=data.get("intent", "single_lookup"),
                needs_retrieval=data.get("needs_retrieval", True),
                needs_tools=data.get("needs_tools", bool(tool)),
                selected_tool=tool,
                tool_args={"query": clean_query} if tool else None,
                sub_questions=data.get("sub_questions", [clean_query]),
                reasoning=f"LLM Classification: {data.get('reasoning', 'Structured classification')}"
            )
        except Exception as e:
            logger.warning(f"LLM Planner classification failed/unavailable: {e}. Falling back to single_lookup.")
            return PlanOutput(
                intent="single_lookup",
                needs_retrieval=True,
                needs_tools=False,
                sub_questions=[clean_query],
                reasoning=f"Fast-path heuristic fallback (LLM error: {str(e)})."
            )


