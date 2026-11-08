from __future__ import annotations

from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "mgs4_remastered_importer"
OUTPUT = ROOT / "dist" / "mgs4_remastered_importer-1.0.0.zip"


def build() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        OUTPUT.unlink()

    with zipfile.ZipFile(
        OUTPUT,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            archive.write(path, path.relative_to(PACKAGE).as_posix())
        archive.write(ROOT / "README.md", "README.md")
        archive.write(ROOT / "LICENSE", "LICENSE")
        archive.write(ROOT / "THIRD_PARTY_NOTICES", "THIRD_PARTY_NOTICES")
    return OUTPUT


if __name__ == "__main__":
    print(build())
