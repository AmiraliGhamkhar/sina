"""Validation for ComfyUI image references served by the authed
``GET /v1/images/file`` route.

Stdlib only — no app imports — so the offline unit test suite can import it
without a full settings/secret environment. ComfyUI's own ``/view`` endpoint
accepts arbitrary ``filename``/``subfolder``/``type`` query params and has a
history of path-traversal bugs, so every part of the reference is whitelisted
here before it is proxied.
"""

import re

# ComfyUI's /view accepts these `type` values; anything else is rejected.
ALLOWED_IMAGE_TYPES = {"output", "temp", "input"}

# One filename, first char alphanumeric (a leading "." would invite hidden-file
# surprises). Space and hyphen are legal in ComfyUI output names.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
# Subfolder is a SINGLE segment — no separators allowed at all.
_SUBFOLDER_RE = re.compile(r"^[A-Za-z0-9._ -]+$")

_FILENAME_MAX_LEN = 255
_SUBFOLDER_MAX_LEN = 200


def validate_image_ref(filename: str, subfolder: str = "", type: str = "output") -> tuple[str, str, str]:
    """Validate a ComfyUI ``/view`` image reference.

    Returns ``(filename, subfolder, type)`` unchanged when every part is safe;
    raises ``ValueError`` with a reason on any violation.
    """
    if not filename:
        raise ValueError("filename is empty")
    if len(filename) > _FILENAME_MAX_LEN:
        raise ValueError(f"filename too long ({len(filename)} > {_FILENAME_MAX_LEN})")
    if not _FILENAME_RE.fullmatch(filename):
        raise ValueError("filename contains invalid characters")
    if ".." in filename:
        raise ValueError("filename may not contain '..'")
    if "/" in filename or "\\" in filename:
        raise ValueError("filename may not contain path separators")

    if subfolder:
        if len(subfolder) > _SUBFOLDER_MAX_LEN:
            raise ValueError(f"subfolder too long ({len(subfolder)} > {_SUBFOLDER_MAX_LEN})")
        if not _SUBFOLDER_RE.fullmatch(subfolder):
            raise ValueError("subfolder contains invalid characters")
        if ".." in subfolder:
            raise ValueError("subfolder may not contain '..'")
        if "/" in subfolder or "\\" in subfolder:
            raise ValueError("subfolder may not contain path separators")

    if type not in ALLOWED_IMAGE_TYPES:
        raise ValueError(f"type must be one of {sorted(ALLOWED_IMAGE_TYPES)}")

    return filename, subfolder, type
