# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import os
import shutil
import tempfile
from pathlib import Path

try:
    import orjson
except ImportError:
    orjson = None
    import json

from mozlog import get_proxy_logger

from .symbolication import ProfileSymbolicator

LOG = get_proxy_logger("profiler")


def save_gecko_profile(profile, filename):
    with open(filename, "wb") as f:
        if orjson is not None:
            f.write(orjson.dumps(profile))
        else:
            f.write(json.dumps(profile).encode("utf-8"))


def symbolicate_profile_json(profile_path, firefox_symbols_path=None):
    """
    Symbolicate a single JSON profile.
    """
    temp_dir = tempfile.mkdtemp()
    windows_symbol_path = os.path.join(temp_dir, "windows")
    os.mkdir(windows_symbol_path)

    symbol_paths = {"FIREFOX": firefox_symbols_path, "WINDOWS": windows_symbol_path}

    symbolicator = ProfileSymbolicator({
        # Trace-level logging (verbose)
        "enableTracing": 0,
        # Fallback server if symbol is not found locally
        "remoteSymbolServer": "https://symbolication.services.mozilla.com/symbolicate/v4",
        # Maximum number of symbol files to keep in memory
        "maxCacheEntries": 2000000,
        # Frequency of checking for recent symbols to
        # cache (in hours)
        "prefetchInterval": 12,
        # Oldest file age to prefetch (in hours)
        "prefetchThreshold": 48,
        # Maximum number of library versions to pre-fetch
        # per library
        "prefetchMaxSymbolsPerLib": 3,
        # Default symbol lookup directories
        "defaultApp": "FIREFOX",
        "defaultOs": "WINDOWS",
        # Paths to .SYM files, expressed internally as a
        # mapping of app or platform names to directories
        # Note: App & OS names from requests are converted
        # to all-uppercase internally
        "symbolPaths": symbol_paths,
    })
    LOG.info("Symbolicating the performance profile...")
    try:
        symbolicator.symbolicate_profile(profile_path)

    except MemoryError:
        LOG.error(
            f"Ran out of memory while trying to symbolicate profile {profile_path}"
        )
    except Exception as e:
        LOG.error("Encountered an exception during profile symbolication")
        LOG.error(e)

    shutil.rmtree(temp_dir)


def symbolicate_profiles(profile_dir=None):
    # Symbolicate all profiles.json in a directory

    if profile_dir is None and os.environ.get("MOZ_UPLOAD_DIR"):
        profile_dir = Path(os.environ.get("MOZ_UPLOAD_DIR"))
    else:
        # Only log error in CI context where we expect MOZ_UPLOAD_DIR
        if os.environ.get("MOZ_AUTOMATION"):
            LOG.error("Profile directory not specified")
        return

    profile_files = sorted(
        profile
        for profile in profile_dir.glob("profile_*.json")
        if "resource-usage" not in profile.name
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        for profile_file in profile_files:
            stat = profile_file.stat()
            unsym_size = stat.st_size
            unsym_mod_time = stat.st_mtime
            unsym_access_time = stat.st_atime
            LOG.info(f"Symbolicating {profile_file.name} ({unsym_size} bytes)...")

            try:
                temp_path = Path(temp_dir) / profile_file.name
                shutil.copy(profile_file, temp_path)

                symbolicate_profile_json(str(temp_path))

                if temp_path.is_file():
                    symbol_size = temp_path.stat().st_size
                    LOG.info(
                        f"Successfully symbolicated {profile_file.name}: "
                        f"{unsym_size} bytes -> {symbol_size} bytes"
                    )
                    profile_file.unlink()
                    shutil.move(str(temp_path), str(profile_file))

                    # To ensure the artifact markers in resource usage profiles are accurate,
                    # the symbolicate profile's mod and access time should reflect
                    # when the artifact was created rather than when the profile
                    # was symbolicated
                    os.utime(profile_file, (unsym_access_time, unsym_mod_time))

            except Exception as e:
                LOG.warning(
                    f"Failed to symbolicate {profile_file.name}: {e}",
                    exc_info=True,
                )
