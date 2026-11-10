import enum


class AllocationQualityGrade(str, enum.Enum):
    """Controlled allocation-only grade; legacy free-text fields remain unchanged."""

    PREMIUM = "PREMIUM"
    STANDARD = "STANDARD"
    PROCESSING = "PROCESSING"
    REJECTED = "REJECTED"
