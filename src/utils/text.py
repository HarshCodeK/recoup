"""Text utilities — slugify and truncate."""


def slugify(text: str) -> str:
    """Lowercase, dashes for whitespace/separators, drop special chars."""
    import re
    # Lowercase, replace any non-alphanumeric run with a single dash,
    # trim leading/trailing dashes.
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")


def truncate(text: str, n: int) -> str:
    """Return `text` truncated to `n` chars plus '...' if longer.

    n <= 0 always returns '...' (or '' for empty text).
    Length <= n returns text unchanged (no ellipsis added).
    """
    if n <= 0:
        return "" if text == "" else "..."
    if len(text) <= n:
        return text
    return text[:n] + "..."