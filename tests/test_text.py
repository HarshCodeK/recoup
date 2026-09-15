"""Tests for src.utils.text — smoke test coverage."""
import pytest
from src.utils.text import slugify, truncate


# ---------- slugify ----------

class TestSlugify:
    def test_lowercase(self):
        assert slugify("HelloWorld") == "helloworld"

    def test_spaces_to_dashes(self):
        assert slugify("hello world") == "hello-world"

    def test_strips_special_chars(self):
        assert slugify("Hello, World!") == "hello-world"

    def test_collapses_multiple_separators(self):
        assert slugify("hello   world---foo") == "hello-world-foo"

    def test_trims_leading_trailing_dashes(self):
        assert slugify("---hello world---") == "hello-world"

    def test_empty_string(self):
        assert slugify("") == ""

    def test_only_special_chars(self):
        assert slugify("!@#$%^&*()") == ""

    def test_unicode_stripped(self):
        # Non-ASCII letters removed -> dashes collapsed -> trimmed
        assert slugify("café résumé") == "caf-r-sum"

    def test_numbers_kept(self):
        assert slugify("Track 4 Plan v2") == "track-4-plan-v2"


# ---------- truncate ----------

class TestTruncate:
    def test_under_limit_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_exact_limit_unchanged(self):
        assert truncate("hello", 5) == "hello"

    def test_over_limit_appends_ellipsis(self):
        assert truncate("hello world", 5) == "hello..."

    def test_truncate_keeps_n_chars_then_ellipsis(self):
        result = truncate("abcdefghij", 3)
        assert result == "abc..."
        assert len(result) == 6  # 3 chars + "..."

    def test_n_zero_with_non_empty(self):
        assert truncate("hello", 0) == "..."

    def test_n_negative_treated_as_zero(self):
        assert truncate("hello", -5) == "..."

    def test_empty_string(self):
        assert truncate("", 10) == ""

    def test_empty_string_with_zero(self):
        assert truncate("", 0) == ""

    def test_n_one(self):
        assert truncate("hello", 1) == "h..."

    def test_unicode_char_boundary_safe(self):
        # 4-byte emoji should not be split
        assert truncate("hi 🚀 there", 3) == "hi ..."