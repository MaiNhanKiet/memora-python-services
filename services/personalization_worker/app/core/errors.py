"""Contract section 3: error code / HTTP mapping (code is the envelope `code`)."""

from __future__ import annotations


class WorkerError(Exception):
    def __init__(self, http_status: int, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.failure_code = failure_code
        self.message = message


def invalid_request(message: str) -> WorkerError:
    return WorkerError(400, "INVALID_REQUEST", message)


def unauthorized() -> WorkerError:
    return WorkerError(401, "UNAUTHORIZED", "Invalid or missing worker secret.")


def object_not_found(key: str) -> WorkerError:
    return WorkerError(404, "OBJECT_NOT_FOUND", f"Object not found: {key}")


def image_unreadable() -> WorkerError:
    return WorkerError(422, "IMAGE_UNREADABLE", "The raw object is not a readable image.")


def fingerprint_region_not_found() -> WorkerError:
    return WorkerError(
        422, "FINGERPRINT_REGION_NOT_FOUND", "No fingerprint ridge region was found in the image."
    )


def audio_unreadable() -> WorkerError:
    return WorkerError(422, "AUDIO_UNREADABLE", "The raw object could not be decoded as audio.")


def audio_duration_invalid(message: str) -> WorkerError:
    return WorkerError(422, "AUDIO_DURATION_INVALID", message)


def segment_out_of_range(message: str) -> WorkerError:
    return WorkerError(422, "SEGMENT_OUT_OF_RANGE", message)


def style_not_generated(style: str) -> WorkerError:
    return WorkerError(
        422,
        "STYLE_NOT_GENERATED",
        f"Wavecode style {style} has not been generated for this source.",
    )


def erase_area_too_large(ratio: float) -> WorkerError:
    return WorkerError(
        422,
        "ERASE_AREA_TOO_LARGE",
        f"Erased {ratio:.1%} of the ridge area; at most 15% may be erased.",
    )


def frame_coverage_too_low(coverage: float) -> WorkerError:
    return WorkerError(
        422,
        "FRAME_COVERAGE_TOO_LOW",
        f"The frame contains {coverage:.0%} ridges; at least 70% is required.",
    )


def version_not_found(version_id: str) -> WorkerError:
    return WorkerError(422, "VERSION_NOT_FOUND", f"Artwork version {version_id} does not exist.")


def storage_unavailable() -> WorkerError:
    return WorkerError(503, "STORAGE_UNAVAILABLE", "Object storage is unavailable.")


def tool_unavailable(tool: str) -> WorkerError:
    return WorkerError(503, "TOOL_UNAVAILABLE", f"Required tool is unavailable: {tool}")
