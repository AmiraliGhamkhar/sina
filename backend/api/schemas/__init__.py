"""API schema package (Pydantic).

Schemas here are the wire contract for the WPF client and any future web UI.
They intentionally mirror — but do not import — ORM models; versioning policy:
breaking changes bump ``/api/v1`` → ``/api/v2`` (never mutate v1 shapes).
"""
