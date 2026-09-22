"""Verify the published Stage 1 foundation files against their frozen manifest."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "artifacts" / "stage1_foundation" / "implementation_manifest.json"


def verify_manifest() -> int:
    manifest = json.loads(MANIFEST.read_bytes())
    normalized = set(manifest["lf_normalized_paths"])
    hashes = manifest["evidence_hashes"]
    if not normalized <= hashes.keys():
        raise AssertionError("Unlisted LF-normalized path")
    for relative, expected in hashes.items():
        path = ROOT / relative
        if path.resolve() == MANIFEST.resolve() or not path.resolve().is_relative_to(ROOT):
            raise AssertionError("Invalid manifest path")
        content = path.read_bytes()
        if relative in normalized:
            content = content.replace(b"\r\n", b"\n")
        if hashlib.sha256(content).hexdigest() != expected:
            raise AssertionError(f"Foundation manifest mismatch: {relative}")
    return len(hashes)


if __name__ == "__main__":
    print(f"Verified {verify_manifest()} foundation hashes")
