"""segmenter.py: determinism, merge behavior, heading glue, fence safety."""

from __future__ import annotations

from mastervault.vaultfs.segmenter import Chunk, segment


def para(i: int, size: int = 180) -> str:
    return f"para-{i:02d} " + ("lorem " * ((size - 8) // 6)).strip()


class TestDeterminism:
    def test_same_body_same_chunks(self):
        body = "\n\n".join(para(i) for i in range(12))
        assert segment(body) == segment(body)

    def test_ordinals_are_sequential(self):
        body = "\n\n".join(para(i) for i in range(12))
        chunks = segment(body)
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))


class TestMergeBehavior:
    def test_small_paragraphs_merge_toward_target(self):
        paras = [para(i) for i in range(10)]
        chunks = segment("\n\n".join(paras), target_chars=600)
        assert 1 < len(chunks) < len(paras)
        assert all(len(c.text) <= 600 for c in chunks)
        # Nothing lost or reordered: chunks re-join to the original paragraphs.
        assert "\n\n".join(c.text for c in chunks) == "\n\n".join(paras)

    def test_never_splits_mid_paragraph(self):
        huge = "x" * 3000
        chunks = segment(f"{para(0)}\n\n{huge}\n\n{para(1)}", target_chars=1200)
        assert huge in [c.text for c in chunks]

    def test_greedy_merge_flushes_at_target(self):
        paras = [para(i, size=180) for i in range(4)]
        chunks = segment("\n\n".join(paras), target_chars=400)
        assert len(chunks) == 2
        assert chunks[0].text == f"{paras[0]}\n\n{paras[1]}"
        assert chunks[1].text == f"{paras[2]}\n\n{paras[3]}"

    def test_empty_body(self):
        assert segment("") == []
        assert segment("\n\n\n") == []


class TestHeadings:
    BODY = (
        "Intro paragraph before any heading.\n"
        "\n"
        "## Alpha\n"
        "\n"
        "Alpha paragraph one.\n"
        "\n"
        "Alpha paragraph two.\n"
        "\n"
        "### Beta\n"
        "\n"
        "Beta paragraph.\n"
    )

    def test_heading_stays_with_its_content(self):
        chunks = segment(self.BODY)
        alpha = next(c for c in chunks if c.text.startswith("## Alpha"))
        assert "Alpha paragraph one." in alpha.text

    def test_merging_never_crosses_headings(self):
        chunks = segment(self.BODY)  # everything is tiny, target is 1200
        texts = [c.text for c in chunks]
        assert texts == [
            "Intro paragraph before any heading.",
            "## Alpha\n\nAlpha paragraph one.\n\nAlpha paragraph two.",
            "### Beta\n\nBeta paragraph.",
        ]

    def test_heading_only_section(self):
        chunks = segment("## Lonely\n")
        assert [c.text for c in chunks] == ["## Lonely"]

    def test_h4_is_not_a_section_boundary(self):
        chunks = segment("Paragraph.\n\n#### Deep heading\n\nMore text.\n")
        assert len(chunks) == 1


class TestCodeFences:
    def test_fenced_block_stays_whole(self):
        body = (
            "Before the fence.\n"
            "\n"
            "```python\n"
            "line one\n"
            "\n"
            "## not a heading\n"
            "line two\n"
            "```\n"
            "\n"
            "After the fence.\n"
        )
        chunks = segment(body)
        joined = "\n\n".join(c.text for c in chunks)
        assert "```python\nline one\n\n## not a heading\nline two\n```" in joined
        assert not any(c.text.startswith("## not a heading") for c in chunks)


def test_chunk_is_frozen_value_object():
    assert Chunk(0, "a") == Chunk(0, "a")
    assert Chunk(0, "a") != Chunk(1, "a")
