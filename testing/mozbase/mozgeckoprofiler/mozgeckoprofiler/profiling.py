# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import os
import shutil
import tempfile

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


def symbolicate_profile_json(profile_path, firefox_symbols_path):
    """
    Symbolicate a single JSON profile.
    """
    print(
        f">>> symbolicate_profile_json called with profile_path={profile_path}, firefox_symbols_path={firefox_symbols_path}"
    )
    import sys

    sys.stdout.flush()
    temp_dir = tempfile.mkdtemp()
    # missing_symbols_zip = os.path.join(temp_dir, "missingsymbols.zip")

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

    LOG.info(
        "Symbolicating the performance profile... This could take a couple of minutes."
    )
    print(f"DEBUG: profile_path={profile_path}")
    print(f"DEBUG: firefox_symbols_path={firefox_symbols_path}")

    try:
        print("DEBUG: Opening profile file...")
        with open(profile_path, "rb") as profile_file:
            if orjson is not None:
                profile = orjson.loads(profile_file.read())
            else:
                profile = json.load(profile_file)
        print(f"DEBUG: Profile loaded, has {len(profile.get('libs', []))} libraries")

        # print("DEBUG: Dumping and integrating missing symbols...")
        # symbolicator.dump_and_integrate_missing_symbols(profile, missing_symbols_zip)

        print("DEBUG: Starting symbolication...")
        symbolicator.symbolicate_profile(profile)
        print("DEBUG: Symbolication complete")

        # Overwrite the profile in place.
        print(f"DEBUG: Saving symbolicated profile to {profile_path}...")
        save_gecko_profile(profile, profile_path)
        print("DEBUG: Profile saved successfully")
    except MemoryError:
        LOG.error(
            f"Ran out of memory while trying to symbolicate profile {profile_path}"
        )
    except Exception as e:
        LOG.error("Encountered an exception during profile symbolication")
        LOG.error(e)

    shutil.rmtree(temp_dir)
