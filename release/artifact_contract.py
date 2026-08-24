from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile


EXPECTED_BUNDLES = {
    "runtime-contract-linux.zip": ("runtime/contract.txt", "linux-runtime-contract\n"),
    "runtime-contract-windows.zip": ("runtime/contract.txt", "windows-runtime-contract\n"),
}


def create_bundles(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, (member, content) in EXPECTED_BUNDLES.items():
        destination = output_dir / filename
        if destination.exists():
            raise SystemExit(f"refusing to overwrite existing artifact contract bundle: {destination}")
        with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(member, content)
    print(f"created {len(EXPECTED_BUNDLES)} artifact contract bundles in {output_dir}")


def verify_bundles(download_dir: Path) -> None:
    if not download_dir.is_dir():
        raise SystemExit(f"artifact contract download directory is missing: {download_dir}")

    bundles = {path.name: path for path in download_dir.rglob("*.zip")}
    expected_names = set(EXPECTED_BUNDLES)
    actual_names = set(bundles)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        raise SystemExit(
            "artifact contract bundle set mismatch: "
            f"missing={missing or 'none'}, unexpected={unexpected or 'none'}"
        )

    for filename, (expected_member, expected_content) in EXPECTED_BUNDLES.items():
        try:
            with ZipFile(bundles[filename]) as archive:
                members = archive.namelist()
                if members != [expected_member]:
                    raise SystemExit(
                        f"artifact contract archive members mismatch for {filename}: {members}"
                    )
                content = archive.read(expected_member).decode("utf-8")
        except BadZipFile as exc:
            raise SystemExit(f"downloaded artifact is not a valid zip file: {filename}") from exc
        if content != expected_content:
            raise SystemExit(f"artifact contract content mismatch for {filename}")

    print(f"verified {len(EXPECTED_BUNDLES)} downloaded artifact contract bundles")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the release artifact upload/download contract")
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.action == "create":
        create_bundles(args.directory)
    else:
        verify_bundles(args.directory)


if __name__ == "__main__":
    main()
