from typing import List, Dict, Any

class EvidenceMerger:
    """
    Deduplicates and merges retrieved evidence chunks across multiple sub-queries.
    """

    @staticmethod
    def merge_evidence(evidence_list: List[Dict[str, Any]], max_evidence: int = 10) -> List[Dict[str, Any]]:
        seen_keys = set()
        unique_evidence = []
        
        for item in evidence_list:
            if not isinstance(item, dict):
                continue
            
            # Key by chunk_id or combination of doc_id and index/content
            chunk_id = item.get("chunk_id") or item.get("id") or item.get("content", "")[:50]
            if chunk_id in seen_keys:
                continue
            
            seen_keys.add(chunk_id)
            unique_evidence.append(item)

        # Sort by reranker_score if present, falling back to rrf_score, similarity, or score
        unique_evidence.sort(
            key=lambda x: (
                x.get("reranker_score") if x.get("reranker_score") is not None else (
                    x.get("rrf_score") if x.get("rrf_score") is not None else (
                        x.get("similarity") if x.get("similarity") is not None else (
                            x.get("score") if x.get("score") is not None else 0.0
                        )
                    )
                )
            ), 
            reverse=True
        )
        
        # Assign 1-indexed citation ids [1], [2], ...
        for idx, doc in enumerate(unique_evidence[:max_evidence], 1):
            doc["citation_id"] = idx

        return unique_evidence[:max_evidence]
