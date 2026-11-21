#!/usr/bin/env python3
"""Explicitly migrate a legacy WiGLE token and publish sanitized configuration."""

import argparse
import json
import os
from pathlib import Path
import tempfile

from cyt_config import load_legacy_config


def migrate_credentials(source: Path, destination: Path) -> bool:
    """Return whether a token was migrated, leaving the source untouched."""
    # lstat also detects a dangling destination symlink.
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        if destination.exists() and destination.samefile(source):
            raise ValueError("destination refers to the source configuration")
        raise FileExistsError(f"destination already exists: {destination}")

    config = load_legacy_config(source, credential_migration=True)
    if "api_keys" not in config:
        return False

    api_keys = config.pop("api_keys")
    if not isinstance(api_keys, dict) or api_keys.keys() != {"wigle"}:
        raise ValueError("only api_keys.wigle.encoded_token can be migrated")
    wigle = api_keys["wigle"]
    if not isinstance(wigle, dict) or wigle.keys() != {"encoded_token"}:
        raise ValueError("only api_keys.wigle.encoded_token can be migrated")
    token = wigle["encoded_token"]
    if not isinstance(token, str) or not token.strip():
        raise ValueError("api_keys.wigle.encoded_token must be a nonblank string")

    # Stage only sanitized data, in the destination directory so publishing
    # can use an atomic hard link that refuses to replace an existing entry.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=".cyt-migration-", delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(config, output, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())

        from secure_credentials import SecureCredentialManager

        SecureCredentialManager().store_credential("wigle", "encoded_token", token)
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink()

    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path,
                        help="new file for sanitized configuration")
    args = parser.parse_args(argv)
    try:
        migrated = migrate_credentials(Path("config.json"), args.destination)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Migration failed: {error}\n")

    if migrated:
        print(f"Sanitized configuration saved to {args.destination}.")
        print("Review it before replacing your original config.json.")
    else:
        print("No api_keys field found. No output was created.")


if __name__ == "__main__":
    main()
