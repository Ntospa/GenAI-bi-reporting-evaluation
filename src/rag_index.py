"""RAG layer: index non-numeric context docs and retrieve relevant chunks.

Uses ChromaDB + Ollama embeddings when available. If either is unavailable, falls back
to returning the raw context file text (small, non-numeric) so the hybrid pipeline still
runs. Numbers never come from here.
"""
from __future__ import annotations
import glob
import os
import re
import requests


def _chunk(text: str):
    # split on markdown headings / blank lines into small context chunks
    parts = re.split(r"\n#+\s|\n\n", text)
    return [p.strip() for p in parts if len(p.strip()) > 30]


def load_context_texts(context_dir: str):
    chunks = []
    for path in glob.glob(os.path.join(context_dir, "*.md")):
        with open(path, encoding="utf-8") as f:
            chunks.extend(_chunk(f.read()))
    return chunks


def _embed(texts, cfg):
    llm = cfg["llm"]
    vecs = []
    for t in texts:
        r = requests.post(f"{llm['base_url']}/api/embeddings",
                          json={"model": llm["embed_model"], "prompt": t}, timeout=120)
        r.raise_for_status()
        vecs.append(r.json()["embedding"])
    return vecs


def build_index(cfg):
    import chromadb  # imported lazily
    g = cfg["grounding"]
    os.makedirs(g["chroma_dir"], exist_ok=True)
    client = chromadb.PersistentClient(path=g["chroma_dir"])
    try:
        client.delete_collection("context")
    except Exception:
        pass
    col = client.create_collection("context")
    chunks = load_context_texts(g["context_dir"])
    embs = _embed(chunks, cfg)
    col.add(ids=[f"c{i}" for i in range(len(chunks))], documents=chunks, embeddings=embs)
    return len(chunks)


def retrieve(query: str, cfg) -> str:
    """Return a context block for the prompt. Falls back to raw context text."""
    g = cfg["grounding"]
    if not g.get("use_rag", True):
        return ""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=g["chroma_dir"])
        col = client.get_collection("context")
        qv = _embed([query], cfg)[0]
        res = col.query(query_embeddings=[qv], n_results=g.get("top_k", 4))
        docs = res.get("documents", [[]])[0]
        return "\n\n".join(docs)
    except Exception:
        # graceful fallback: use the (small, non-numeric) context files directly
        chunks = load_context_texts(g["context_dir"])
        return "\n\n".join(chunks[: g.get("top_k", 4)])


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml"), encoding="utf-8"))
    n = build_index(cfg)
    print(f"Indexed {n} context chunks into {cfg['grounding']['chroma_dir']}")
