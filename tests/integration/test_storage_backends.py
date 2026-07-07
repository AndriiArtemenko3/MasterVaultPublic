"""Integration tests for both storage backends (sqlite + postgres).

The `backend` fixture (tests/conftest.py) parametrizes every test over both
backends; postgres tests skip only when DATABASE_URL is unset or unreachable.
"""

from __future__ import annotations

import pytest

from mastervault.models import content_hash
from mastervault.storage.base import (
    AliasRow,
    ChunkRow,
    ClaimRow,
    DocumentRow,
    EmbeddingRow,
    SchemaMismatchError,
)

pytestmark = pytest.mark.integration

DIM = 8


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def basis(i: int, dim: int = DIM) -> list[float]:
    v = [0.0] * dim
    v[i] = 1.0
    return v


def doc_row(
    doc_id: str,
    rel_path: str,
    *,
    domain: str = "operations",
    doc_type: str = "source",
    title: str = "Untitled",
    body: str = "placeholder body",
) -> DocumentRow:
    return DocumentRow(
        doc_id=doc_id,
        doc_type=doc_type,
        domain=domain,
        rel_path=rel_path,
        title=title,
        frontmatter={"title": title, "domain": domain},
        body=body,
        content_hash=content_hash(body),
    )


def claim_row(claim_id: str, doc_id: str, ordinal: int, statement: str,
              confidence: str = "medium", affects: list[str] | None = None) -> ClaimRow:
    return ClaimRow(
        claim_id=claim_id,
        doc_id=doc_id,
        ordinal=ordinal,
        statement=statement,
        confidence=confidence,
        content_hash=content_hash(statement),
        affects=affects or [],
    )


def chunk_row(chunk_id: str, doc_id: str, ordinal: int, text: str) -> ChunkRow:
    return ChunkRow(
        chunk_id=chunk_id,
        doc_id=doc_id,
        ordinal=ordinal,
        text=text,
        content_hash=content_hash(text),
    )


def emb_row(record_id: str, vector: list[float], *, record_type: str = "claim",
            doc_id: str | None = None, domain: str | None = "operations",
            model: str = "test-embed-v1", text: str = "") -> EmbeddingRow:
    return EmbeddingRow(
        record_id=record_id,
        record_type=record_type,
        doc_id=doc_id,
        domain=domain,
        content_hash=content_hash(text or record_id),
        model_version=model,
        vector=vector,
    )


def seed_zebra_doc(backend) -> tuple[DocumentRow, list[ClaimRow], list[ChunkRow]]:
    doc = doc_row(
        "source:operations/sources/zebra-note.md",
        "operations/sources/zebra-note.md",
        domain="operations",
        title="Zebra corridor field note",
        body="The zebra migration corridor mapping must be refreshed after every wet season.",
    )
    claims = [
        claim_row("zebra-note-01", doc.doc_id, 0,
                  "The zebra migration corridor mapping is refreshed quarterly.",
                  confidence="high", affects=["migration-corridor"]),
        claim_row("zebra-note-02", doc.doc_id, 1,
                  "Field teams reported broken fences along the northern corridor.",
                  confidence="medium",
                  affects=["migration-corridor", "fence-maintenance"]),
    ]
    chunks = [chunk_row(f"chunk:{doc.doc_id}#0", doc.doc_id, 0, doc.body)]
    backend.upsert_document(doc, claims, chunks, [])
    return doc, claims, chunks


def seed_refund_doc(backend) -> tuple[DocumentRow, list[ClaimRow]]:
    doc = doc_row(
        "source:customer-support/sources/refund-faq.md",
        "customer-support/sources/refund-faq.md",
        domain="customer-support",
        title="Refund FAQ",
        body="Our refund policy window is thirty days from delivery confirmation.",
    )
    claims = [
        claim_row("refund-faq-01", doc.doc_id, 0,
                  "The refund policy window is thirty days from delivery.",
                  confidence="high", affects=["refund-policy"]),
    ]
    backend.upsert_document(doc, claims, [], [])
    return doc, claims


def seed_wiki_doc(backend) -> DocumentRow:
    doc = doc_row(
        "wiki:operations:migration-corridor",
        "operations/wiki/migration-corridor.md",
        domain="operations",
        doc_type="wiki",
        title="Migration corridor",
        body="Canonical entry for the seasonal migration corridor.",
    )
    aliases = [
        AliasRow(alias="Migration Corridor", wiki_slug="migration-corridor",
                 domain="operations"),
        AliasRow(alias="Corridor Map", wiki_slug="migration-corridor", domain="operations"),
    ]
    backend.upsert_document(doc, [], [], aliases)
    return doc


def claim_items(claims: list[ClaimRow]) -> list[tuple[str, str]]:
    return [(f"claim:{c.claim_id}", c.content_hash) for c in claims]


# ---------------------------------------------------------------------------
# init_schema
# ---------------------------------------------------------------------------


def test_init_schema_idempotent_rerun(backend, dim, model_version):
    doc, claims, _ = seed_zebra_doc(backend)
    backend.init_schema(dim, model_version)
    backend.init_schema(dim, model_version)
    assert [d.doc_id for d in backend.get_documents([doc.doc_id])] == [doc.doc_id]
    assert backend.stats()["counts"]["claims"] == len(claims)


def test_init_schema_refuses_dim_mismatch(backend, dim, model_version):
    with pytest.raises(SchemaMismatchError, match=str(dim)):
        backend.init_schema(dim + 8, model_version)


def test_init_schema_refuses_model_mismatch(backend, dim, model_version):
    with pytest.raises(SchemaMismatchError, match=model_version):
        backend.init_schema(dim, "some-other-model-v9")


# ---------------------------------------------------------------------------
# needs_embedding — idempotency crown jewel
# ---------------------------------------------------------------------------


def test_needs_embedding_full_lifecycle(backend, model_version):
    _, claims, _ = seed_zebra_doc(backend)
    items = claim_items(claims)
    assert backend.needs_embedding(items, model_version) == [rid for rid, _ in items]

    backend.upsert_embeddings([
        emb_row(rid, basis(i), doc_id=claims[0].doc_id, model=model_version,
                text=claims[i].statement)
        for i, (rid, _) in enumerate(items)
    ])
    # re-fetch with the stored hashes: nothing to embed
    items = claim_items(claims)
    assert backend.needs_embedding(items, model_version) == []
    # model bump: everything is stale again
    assert backend.needs_embedding(items, "next-model-v2") == [rid for rid, _ in items]


def test_needs_embedding_returns_exactly_the_changed_record(backend, model_version):
    _, claims, _ = seed_zebra_doc(backend)
    items = claim_items(claims)
    backend.upsert_embeddings([
        emb_row(rid, basis(i), doc_id=claims[0].doc_id, model=model_version,
                text=claims[i].statement)
        for i, (rid, _) in enumerate(items)
    ])
    changed = [(items[0][0], content_hash("an edited statement")), items[1]]
    assert backend.needs_embedding(changed, model_version) == [items[0][0]]


# ---------------------------------------------------------------------------
# upsert_document / delete_documents_not_in
# ---------------------------------------------------------------------------


def test_document_replace_removes_stale_claims(backend):
    doc, claims, _ = seed_zebra_doc(backend)
    replacement = [
        claim_row("zebra-note-02", doc.doc_id, 0,
                  "Field teams reported broken fences along the northern corridor.",
                  confidence="medium", affects=["fence-maintenance"]),
        claim_row("zebra-note-03", doc.doc_id, 1,
                  "Ranger patrols now photograph every waypoint on the loop.",
                  confidence="low", affects=["migration-corridor"]),
    ]
    backend.upsert_document(doc, replacement, [], [])

    assert backend.get_claims(["zebra-note-01"]) == []
    got = backend.get_claims(["zebra-note-02", "zebra-note-03"])
    assert [c.claim_id for c in got] == ["zebra-note-02", "zebra-note-03"]
    # FTS stays in sync: the dropped claim's unique phrase no longer matches
    assert "zebra-note-01" not in backend.lexical_claims("refreshed quarterly", k=5)
    assert backend.lexical_claims("ranger patrols waypoint", k=5) == ["zebra-note-03"]
    # graph edges follow the replacement
    assert backend.claims_for_wiki(["migration-corridor"], k=10) == ["zebra-note-03"]


def test_document_replace_removes_stale_aliases(backend):
    wiki = seed_wiki_doc(backend)
    assert "corridor map" in backend.alias_index()
    backend.upsert_document(
        wiki, [], [],
        [AliasRow(alias="Migration Corridor", wiki_slug="migration-corridor",
                  domain="operations")],
    )
    idx = backend.alias_index()
    assert "corridor map" not in idx
    assert idx["migration corridor"] == ("migration-corridor", "operations")


def test_delete_documents_not_in_cascades(backend, model_version):
    zebra_doc, zebra_claims, zebra_chunks = seed_zebra_doc(backend)
    refund_doc, refund_claims = seed_refund_doc(backend)
    wiki_doc = seed_wiki_doc(backend)
    backend.upsert_embeddings([
        emb_row(f"claim:{refund_claims[0].claim_id}", basis(3), doc_id=refund_doc.doc_id,
                domain="customer-support", model=model_version),
    ])

    removed = backend.delete_documents_not_in({zebra_doc.rel_path, wiki_doc.rel_path})

    assert removed == [refund_doc.doc_id]
    assert backend.get_documents([refund_doc.doc_id]) == []
    assert backend.get_claims([refund_claims[0].claim_id]) == []
    assert backend.claims_for_wiki(["refund-policy"], k=10) == []
    assert backend.lexical_docs("refund policy window", k=5) == []
    # the cascaded embedding is gone from both metadata and the vector index
    assert backend.needs_embedding(
        [(f"claim:{refund_claims[0].claim_id}", refund_claims[0].content_hash)],
        model_version,
    ) == [f"claim:{refund_claims[0].claim_id}"]
    assert backend.knn(basis(3), k=3) == []
    # survivors intact
    assert backend.get_documents([zebra_doc.doc_id]) != []
    assert len(backend.get_claims([c.claim_id for c in zebra_claims])) == 2
    assert len(backend.get_chunks([zebra_chunks[0].chunk_id])) == 1


def test_delete_documents_not_in_with_empty_set_removes_all(backend):
    seed_zebra_doc(backend)
    seed_refund_doc(backend)
    removed = backend.delete_documents_not_in(set())
    assert len(removed) == 2
    assert backend.stats()["counts"]["documents"] == 0


# ---------------------------------------------------------------------------
# knn
# ---------------------------------------------------------------------------


def test_knn_exact_match_first(backend, model_version):
    doc, claims, _ = seed_zebra_doc(backend)
    backend.upsert_embeddings([
        emb_row("claim:zebra-note-01", basis(0), doc_id=doc.doc_id, model=model_version),
        emb_row("claim:zebra-note-02", basis(1), doc_id=doc.doc_id, model=model_version),
        emb_row(f"chunk:{doc.doc_id}#0", [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                record_type="chunk", doc_id=doc.doc_id, model=model_version),
    ])
    hits = backend.knn(basis(0), k=3)
    assert [rid for rid, _ in hits] == [
        "claim:zebra-note-01", f"chunk:{doc.doc_id}#0", "claim:zebra-note-02",
    ]
    sims = [s for _, s in hits]
    assert sims[0] == pytest.approx(1.0, abs=1e-3)
    assert sims == sorted(sims, reverse=True)
    assert sims[-1] == pytest.approx(0.0, abs=1e-3)  # orthogonal


def test_knn_filters_by_domain_and_record_type(backend, model_version):
    zebra_doc, _, _ = seed_zebra_doc(backend)
    refund_doc, _ = seed_refund_doc(backend)
    wiki_doc = seed_wiki_doc(backend)
    backend.upsert_embeddings([
        emb_row("claim:zebra-note-01", basis(0), doc_id=zebra_doc.doc_id,
                domain="operations", model=model_version),
        emb_row("claim:refund-faq-01", basis(0), doc_id=refund_doc.doc_id,
                domain="customer-support", model=model_version),
        emb_row("wiki:operations:migration-corridor", basis(0), record_type="wiki",
                doc_id=wiki_doc.doc_id, domain="operations", model=model_version),
    ])
    ops_hits = backend.knn(basis(0), k=5, domain="operations")
    assert {rid for rid, _ in ops_hits} == {
        "claim:zebra-note-01", "wiki:operations:migration-corridor",
    }
    wiki_hits = backend.knn(basis(0), k=5, record_types=["wiki"])
    assert [rid for rid, _ in wiki_hits] == ["wiki:operations:migration-corridor"]


# ---------------------------------------------------------------------------
# lexical
# ---------------------------------------------------------------------------


def test_lexical_docs_relevance(backend):
    zebra_doc, _, _ = seed_zebra_doc(backend)
    refund_doc, _ = seed_refund_doc(backend)
    hits = backend.lexical_docs("refund policy window", k=5)
    assert hits and hits[0] == refund_doc.doc_id
    assert zebra_doc.doc_id not in hits


def test_lexical_claims_relevance_and_domain_filter(backend):
    seed_zebra_doc(backend)
    seed_refund_doc(backend)
    hits = backend.lexical_claims("broken fences", k=5)
    assert hits and hits[0] == "zebra-note-02"
    assert backend.lexical_claims("broken fences", k=5, domain="customer-support") == []
    assert backend.lexical_claims("refund", k=5, domain="customer-support") == ["refund-faq-01"]


@pytest.mark.parametrize(
    "query",
    [
        '"refund" policy!!! (urgent?)',
        "it's -- a; test's edge",
        '"""',
        "?!,;:()[]",
        'zebra\'s "corridor" AND OR NOT NEAR(x y)',
        "",
    ],
)
def test_lexical_tolerates_punctuation_queries(backend, query):
    seed_zebra_doc(backend)
    seed_refund_doc(backend)
    assert isinstance(backend.lexical_claims(query, k=5), list)
    assert isinstance(backend.lexical_docs(query, k=5), list)


# ---------------------------------------------------------------------------
# graph + aliases + hydration
# ---------------------------------------------------------------------------


def test_claims_for_wiki_confidence_ordering(backend):
    seed_zebra_doc(backend)  # zebra-note-01 high, zebra-note-02 medium
    got = backend.claims_for_wiki(["migration-corridor"], k=10)
    assert got == ["zebra-note-01", "zebra-note-02"]
    assert backend.claims_for_wiki(["fence-maintenance"], k=10) == ["zebra-note-02"]
    assert backend.claims_for_wiki(["migration-corridor"], k=1) == ["zebra-note-01"]
    assert backend.claims_for_wiki([], k=10) == []


def test_alias_index_round_trip(backend):
    seed_wiki_doc(backend)
    idx = backend.alias_index()
    assert idx["migration corridor"] == ("migration-corridor", "operations")
    assert idx["corridor map"] == ("migration-corridor", "operations")


def test_hydration_returns_full_rows(backend):
    zebra_doc, zebra_claims, zebra_chunks = seed_zebra_doc(backend)

    docs = backend.get_documents([zebra_doc.doc_id, "source:missing.md"])
    assert len(docs) == 1
    assert docs[0].rel_path == zebra_doc.rel_path
    assert docs[0].frontmatter["domain"] == "operations"
    assert docs[0].body == zebra_doc.body

    claims = backend.get_claims(["zebra-note-02", "zebra-note-01"])
    assert [c.claim_id for c in claims] == ["zebra-note-02", "zebra-note-01"]  # input order
    assert claims[0].statement.startswith("Field teams")
    assert claims[0].confidence == "medium"
    assert claims[0].rel_path == zebra_doc.rel_path
    assert claims[0].domain == "operations"
    assert claims[0].affects == ["fence-maintenance", "migration-corridor"]

    chunks = backend.get_chunks([zebra_chunks[0].chunk_id])
    assert chunks[0].text == zebra_doc.body
    assert chunks[0].rel_path == zebra_doc.rel_path
    assert chunks[0].domain == "operations"


# ---------------------------------------------------------------------------
# stats / wipe
# ---------------------------------------------------------------------------


def test_wipe_truncates_everything_but_stays_usable(backend, dim, model_version):
    doc, claims, _ = seed_zebra_doc(backend)
    seed_wiki_doc(backend)
    backend.upsert_embeddings([
        emb_row("claim:zebra-note-01", basis(0), doc_id=doc.doc_id, model=model_version),
    ])

    backend.wipe()

    counts = backend.stats()["counts"]
    assert all(n == 0 for n in counts.values())
    assert backend.knn(basis(0), k=3) == []
    assert backend.alias_index() == {}
    assert backend.needs_embedding(claim_items(claims), model_version) == [
        f"claim:{c.claim_id}" for c in claims
    ]
    # meta identity survives: re-init with the same dim/model is still accepted
    backend.init_schema(dim, model_version)
    seed_zebra_doc(backend)
    assert backend.stats()["counts"]["documents"] == 1
