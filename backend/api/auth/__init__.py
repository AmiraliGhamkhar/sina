"""Authentication package.

Phase 1 ships the token codec + request dependency so every route already
codes against the final contract. Password hashing, refresh-token rotation,
and user persistence land in Phase 7 (auth/models are specified there).
"""
