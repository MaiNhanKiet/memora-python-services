from pathlib import Path


def _read_version() -> str:
    path = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or "1.0.0"


PROCESSOR_VERSION = _read_version()
