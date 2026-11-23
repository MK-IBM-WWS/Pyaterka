#!/usr/bin/env python3
"""Prepare pinned, local browser assets; no Node.js or runtime CDN is needed."""

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "static" / "vendor"
MANIFEST = ROOT / "frontend-assets.json"
PACKAGES = {
    "livekit-client": {
        "version": "2.5.0",
        "integrity": "4E8wiTstl2KLilYmrBgBy8G328Y6A92ysv6SLXkz/k54n4xguL5RS3qdmKNkPFbiL5xW22yw6dwf4Vf/j34eOA==",
    },
}


def download_package(name):
    package = PACKAGES[name]
    url = (
        f"https://registry.npmjs.org/{name}/-/"
        f"{name}-{package['version']}.tgz"
    )
    print(f"Downloading {name}@{package['version']} ...", flush=True)
    request = Request(url, headers={"User-Agent": "ai-voice-assets/1.0"})
    with urlopen(request, timeout=60) as response:
        data = response.read()
    digest = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != package["integrity"]:
        raise ValueError(f"Checksum mismatch for {name}; assets were not installed")
    # Never extract an untrusted path from a tar archive to the filesystem.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        return {
            item.name: archive.extractfile(item).read()
            for item in archive.getmembers()
            if item.isfile() and item.name.startswith("package/")
        }


def select_assets():
    livekit = download_package("livekit-client")
    return {
        "livekit-client-2.5.0.esm.mjs": livekit["package/dist/livekit-client.esm.mjs"],
        "livekit-client.LICENSE": livekit["package/LICENSE"],
    }


def missing_assets():
    if not MANIFEST.is_file():
        return ["frontend-assets.json"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("versions") != {
        name: package["version"] for name, package in PACKAGES.items()
    }:
        return ["asset versions"]
    missing = []
    for filename, digest in manifest["sha256"].items():
        path = VENDOR / filename
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            missing.append(filename)
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check bundled files without downloading")
    args = parser.parse_args()
    missing = missing_assets()
    if not missing:
        print("Local browser assets are ready (LiveKit 2.5.0).")
        return 0
    if args.check:
        print("Missing or damaged browser assets: " + ", ".join(missing), file=sys.stderr)
        return 1
    assets = select_assets()
    for filename, data in assets.items():
        path = VENDOR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    MANIFEST.write_text(
        json.dumps(
            {
                "versions": {name: package["version"] for name, package in PACKAGES.items()},
                "sha256": {
                    name: hashlib.sha256(data).hexdigest()
                    for name, data in sorted(assets.items())
                },
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {len(assets)} local browser assets.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Cannot prepare browser assets: {error}", file=sys.stderr)
        sys.exit(1)
