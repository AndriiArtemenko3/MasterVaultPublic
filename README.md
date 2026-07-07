# MasterVault

Internal-OS agentic RAG for small businesses. Markdown files are the canonical
knowledge store; PostgreSQL + pgvector is the derived index; retrieval is hybrid
(lexical + graph + vector, RRF-fused); ingestion and Q&A run through an agentic
pipeline with human-in-the-loop review.

> **Status: under construction.** This README will be replaced by the full
> quickstart + 10-minute tour once the demo dataset lands.

## Planned quickstart

```bash
uv sync
docker compose up -d          # optional: Postgres+pgvector (SQLite works without it)
mvault init
mvault demo load              # loads the Larkstead Goods Co. synthetic dataset
mvault search "refund window"
mvault ask "What is our current refund window, and when did it change?"
```
