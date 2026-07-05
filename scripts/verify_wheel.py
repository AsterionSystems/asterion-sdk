"""Verify the asterion-ccsds wheel layout and core metadata."""

from __future__ import annotations

import sys
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


def verify_wheel(wheel_path: Path) -> None:
    with ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        unexpected = {
            name
            for name in names
            if not name.startswith(
                ("asterion/ccsds/", "asterion_ccsds-0.1.0.dist-info/")
            )
        }
        assert not unexpected, f"unexpected wheel entries: {sorted(unexpected)}"
        assert "asterion/__init__.py" not in names
        assert "asterion/ccsds/py.typed" in names

        metadata_path = "asterion_ccsds-0.1.0.dist-info/METADATA"
        metadata = BytesParser().parsebytes(wheel.read(metadata_path))
        assert metadata["Name"] == "asterion-ccsds"
        assert metadata["Version"] == "0.1.0"
        assert metadata["Requires-Python"] == ">=3.12"
        assert metadata["License-Expression"] == "Apache-2.0"

        license_paths = [
            name for name in names if name.endswith(".dist-info/licenses/LICENSE")
        ]
        assert len(license_paths) == 1, "wheel must contain exactly one LICENSE"
        license_text = wheel.read(license_paths[0]).decode()
        assert "Apache License" in license_text
        assert "Version 2.0" in license_text


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_wheel.py WHEEL_PATH")
    verify_wheel(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
