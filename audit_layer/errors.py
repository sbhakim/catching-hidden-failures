"""Unavailable verification is distinct from a curricular violation."""

class AuditUnavailable(RuntimeError):
    """A required check could not be completed; no candidate is certified."""
