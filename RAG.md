# RAG — Retrieval-Augmented Generation

**RAG = LLM + Knowledge Base + Retrieval.** Instead of answering from the model's
general training, the gateway retrieves the most relevant pieces of a local knowledge
base and asks the LLM to answer **only from that context** — grounded, source-cited
answers about *your* information.

## How it works in this project

```
question ──► embed query ──► cosine-rank KB chunks ──► top-k context
                                                          │
                                            build grounded prompt
                                                          │
                                                  local LLM (LM Studio)
                                                          │
                                              answer + cited sources
```

1. **Knowledge base:** `rag/knowledge_base.md` (your CV / profile). Edit it freely.
2. **Indexing:** on first use the file is split into overlapping chunks and each chunk
   is embedded once via the embedding model (`text-embedding-nomic-embed-text-v1.5` in
   LM Studio). Vectors are held in memory.
3. **Retrieval:** the question is embedded, cosine-similarity ranks chunks, top-k
   (default 4) become the context.
4. **Generation:** the LLM answers strictly from that context and cites `[Source N]`.
   If the answer isn't in the knowledge base, it says so.

## Configuration (.env)
```
EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
RAG_KB_PATH=rag/knowledge_base.md
RAG_TOP_K=4
RAG_CHUNK_CHARS=700
RAG_CHUNK_OVERLAP=120
```
Works with both backends: LM Studio/OpenAI (`/v1/embeddings`) or Ollama (`/api/embeddings`).
The embedding model must be available on the running LLM server.

## Endpoints (all under `/v1/rag`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/v1/rag` | API key | Ask a question; returns answer + sources |
| POST | `/v1/rag/stream` | API key | Same, streamed (SSE) |
| GET | `/v1/rag/status` | API key | Index ready? chunk count |
| POST | `/v1/rag/reindex` | admin key | Rebuild after editing the KB |

## Use it

Ask a question (grounded in the knowledge base):
```bash
curl -X POST http://127.0.0.1:8000/v1/rag \
  -H "X-API-KEY: AI-..." -H "Content-Type: application/json" \
  -d '{"prompt":"Which databases does he know?"}'
```
Response:
```json
{
  "success": true,
  "response": "He works with Microsoft SQL Server, PostgreSQL, and Oracle (including Oracle APEX) [Source 1].",
  "sources": [{"section":"Databases","score":0.82,"preview":"..."}]
}
```

Rebuild the index after editing `rag/knowledge_base.md`:
```bash
curl -X POST http://127.0.0.1:8000/v1/rag/reindex -H "X-ADMIN-KEY: <ADMIN_API_KEY>"
```

## Editing the knowledge base
Open `rag/knowledge_base.md`, replace placeholders (name, contact, real experience),
add any documents you want the assistant to know, then `reindex`. You can paste large
amounts of text — it is chunked automatically.

## Notes
- The knowledge base stays **100% local**; nothing leaves your machine.
- Keep the LM Studio server running with the embedding model available.
- For large knowledge bases, raise `RAG_TOP_K` and consider a persistent vector store
  (current store is in-memory, rebuilt on reindex/restart).
