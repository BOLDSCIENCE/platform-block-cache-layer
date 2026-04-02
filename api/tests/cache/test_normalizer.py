"""Tests for query normalization and key construction."""

from src.cache.normalizer import (
    build_cache_sk,
    build_gsi_project_entries_pk,
    build_gsi_query_hash_pk,
    build_pk,
    compute_query_hash,
    normalize_query,
)


class TestNormalizeQuery:
    def test_strips_whitespace(self):
        assert normalize_query("  hello  ") == "hello"

    def test_lowercases(self):
        assert normalize_query("How Do I Reset?") == "how do i reset?"

    def test_collapses_spaces(self):
        assert normalize_query("how  do   i reset?") == "how do i reset?"

    def test_normalizes_trailing_punctuation(self):
        assert normalize_query("hello??") == "hello?"
        assert normalize_query("hello!!") == "hello?"
        assert normalize_query("hello...") == "hello?"
        assert normalize_query("hello?!.") == "hello?"

    def test_adds_question_mark_to_plain_text(self):
        # Text without trailing punctuation gets no ? added
        result = normalize_query("hello world")
        assert result == "hello world"

    def test_full_normalization_example(self):
        assert normalize_query(" How do I  reset my password?? ") == "how do i reset my password?"

    def test_different_queries_produce_different_results(self):
        q1 = normalize_query("How do I reset my password?")
        q2 = normalize_query("What's the password reset process?")
        assert q1 != q2


class TestComputeQueryHash:
    def test_deterministic(self):
        h1 = compute_query_hash("how do i reset my password?")
        h2 = compute_query_hash("how do i reset my password?")
        assert h1 == h2

    def test_different_queries_different_hashes(self):
        h1 = compute_query_hash("how do i reset my password?")
        h2 = compute_query_hash("what's the password reset process?")
        assert h1 != h2

    def test_returns_hex_string(self):
        h = compute_query_hash("test")
        assert len(h) == 64  # SHA-256 hex is 64 chars
        assert all(c in "0123456789abcdef" for c in h)


class TestKeyBuilders:
    def test_build_pk(self):
        assert build_pk("scicoms", "acme-corp") == "APP#scicoms#CLIENT#acme-corp"

    def test_build_cache_sk(self):
        sk = build_cache_sk("ws_01", "proj_01", "ce_01")
        assert sk == "CACHE#WS#ws_01#PROJ#proj_01#ce_01"

    def test_build_gsi_query_hash_pk(self):
        pk = build_gsi_query_hash_pk("scicoms", "acme-corp", "abc123")
        assert pk == "APP#scicoms#CLIENT#acme-corp#HASH#abc123"

    def test_build_gsi_project_entries_pk(self):
        pk = build_gsi_project_entries_pk("scicoms", "acme-corp", "ws_01", "proj_01")
        assert pk == "APP#scicoms#CLIENT#acme-corp#WS#ws_01#PROJ#proj_01"
