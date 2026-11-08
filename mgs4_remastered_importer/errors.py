class MGS4Error(Exception):
    """Base error for invalid or unsupported MGS4 data."""


class FormatError(MGS4Error):
    """Raised when a file is malformed or has an unsupported layout."""


class AssetNotFoundError(MGS4Error):
    """Raised when a requested game asset cannot be resolved."""
