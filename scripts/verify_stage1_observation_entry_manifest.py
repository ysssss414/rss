"""Verify public Stage 1 observation/entry evidence against frozen local files."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "artifacts/stage1_observation_entry/qualification_manifest.json"


def verify_manifest() -> int:
    manifest = json.loads(MANIFEST.read_bytes())
    normalized = set(manifest["lf_normalized_paths"])
    hashes = manifest["evidence_hashes"]
    if not normalized <= hashes.keys():
        raise AssertionError("Unlisted LF-normalized path")
    for relative, expected in hashes.items():
        path = (ROOT / relative).resolve()
        if path == MANIFEST.resolve() or not path.is_relative_to(ROOT):
            raise AssertionError("Invalid observation/entry manifest path")
        content = path.read_bytes()
        if relative in normalized:
            content = content.replace(b"\r\n", b"\n")
        if hashlib.sha256(content).hexdigest() != expected:
            raise AssertionError(f"Observation/entry manifest mismatch: {relative}")
    return len(hashes)


if __name__ == "__main__":
    print(f"Verified {verify_manifest()} observation/entry hashes")
