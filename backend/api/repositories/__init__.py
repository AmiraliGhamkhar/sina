"""Data-access layer — Phase 7 (SQLAlchemy async repositories).

Repositories are the only modules allowed to hold ``AsyncSession`` usage;
routes/services never touch the engine directly.
"""
