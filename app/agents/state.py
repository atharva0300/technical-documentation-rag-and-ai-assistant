from typing import TypedDict, List, Dict, Any, Optional
from typing_extensions import Annotated
import operator

class V2AgentState(TypedDict):
    conversation_id: str
    query: str
    rewritten_query: Optional[str]
    history: List[Dict[str, str]]
    
    intent: Optional[str]  # single_lookup, multi_hop, comparison, chitchat, out_of_scope
    retrieval_strategy: Optional[str]  # point_lookup, exhaustive, relational (see app/graph/)
    router_category: Optional[str]  # semantic router's confident category, None if not confident
    needs_retrieval: bool
    needs_tools: bool
    selected_tool: Optional[str]
    tool_args: Optional[Dict[str, Any]]
    sub_questions: List[str]
    filters: Optional[Dict[str, Any]]
    
    # Evidence for the CURRENT attempt only. Deliberately NOT an operator.add reducer:
    # each node that writes this key (mcp_executor_node, retrieve_node) replaces it rather
    # than appending, so a verifier RETRY starts from a clean evidence set instead of piling
    # new results onto stale ones from the previous attempt (which also shifted citation
    # numbering between attempts). retrieve_node explicitly folds in mcp_executor_node's
    # contribution on the first pass only (see retrieve_node for why).
    evidence: List[Dict[str, Any]]
    
    answer_draft: Optional[str]
    citations: List[Dict[str, Any]]
    
    verification_status: Optional[str]  # VALID, PARTIAL, RETRY, REFUSE
    verification_feedback: Optional[str]
    retry_reason: Optional[str]  # "citation_format" | "ungrounded_content" | None
    unsupported_sentences: List[str]
    
    # Execution budgets & trace
    step_count: int
    llm_call_count: int
    tool_call_count: int
    retry_count: int
    trace_events: Annotated[List[Dict[str, Any]], operator.add]
    error: Optional[str]
    stream_handler: Optional[Any]
