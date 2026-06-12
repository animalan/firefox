# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import gzip
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
    debug_log_path = os.path.join(
        os.environ.get("MOZ_UPLOAD_DIR", "/tmp"), "profiling_debug.log"
    )

    def debug_log(msg):
        print(msg)
        with open(debug_log_path, "a") as f:
            f.write(msg + "\n")
        import sys

        sys.stdout.flush()

    debug_log(
        f">>> symbolicate_profile_json called with profile_path={profile_path}, firefox_symbols_path={firefox_symbols_path}"
    )
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
    debug_log(f"DEBUG: profile_path={profile_path}")
    debug_log(f"DEBUG: firefox_symbols_path={firefox_symbols_path}")

    try:
        debug_log("DEBUG: Opening profile file...")
        with open(profile_path, "rb") as profile_file:
            data = profile_file.read()
            try:
                data = gzip.decompress(data)
                debug_log("DEBUG: Profile was gzipped, decompressed successfully")
            except Exception as e:
                debug_log(f"DEBUG: Profile is not gzipped, using as-is ({e})")
            if orjson is not None:
                profile = orjson.loads(data)
            else:
                profile = json.loads(data)
        debug_log(
            f"DEBUG: Profile loaded, has {len(profile.get('libs', []))} libraries"
        )

        # debug_log("DEBUG: Dumping and integrating missing symbols...")
        # symbolicator.dump_and_integrate_missing_symbols(profile, missing_symbols_zip)

        debug_log("DEBUG: Starting symbolication...")
        symbolicator.symbolicate_profile(profile)
        debug_log("DEBUG: Symbolication complete")

        # Overwrite the profile in place.
        debug_log(f"DEBUG: Saving symbolicated profile to {profile_path}...")
        save_gecko_profile(profile, profile_path)
        debug_log("DEBUG: Profile saved successfully")
    except MemoryError as e:
        debug_log(f"ERROR: Out of memory: {e}")
        LOG.error(
            f"Ran out of memory while trying to symbolicate profile {profile_path}"
        )
    except Exception as e:
        debug_log(f"ERROR: Exception during symbolication: {e}")
        LOG.error("Encountered an exception during profile symbolication")
        LOG.error(e)

    shutil.rmtree(temp_dir)
