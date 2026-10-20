import os
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

# all-MiniLM-L6-v2 was trained for general sentence similarity (STS), not
# specifically for retrieval ranking. bge-base-en-v1.5 is trained with a
# contrastive query-passage retrieval objective and measurably outperforms
# MiniLM on MTEB's retrieval benchmark -- a documented gap, not a guess.
# Staying general-purpose (not a code/domain-specific model) matters here
# because this project accepts arbitrary uploaded technical documentation,
# not one fixed domain. NOTE: this is a 768-dim model (MiniLM was 384-dim) --
# switching requires re-embedding the corpus; see docs/ENVIRONMENT.md.
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")

_model = None

def get_embedding_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")
    return _model

def generate_embedding(text: str) -> list[float]:
    model = get_embedding_model()
    embedding = model.encode(text)
    return embedding.tolist()

def generate_embeddings_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    if not texts:
        return []
    model = get_embedding_model()
    embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=False)
    return [e.tolist() for e in embeddings]
