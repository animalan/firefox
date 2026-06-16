# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import os
import shutil
import sys
import tempfile
import time

try:
    import orjson
except ImportError:
    orjson = None
    import json
import json

from mozlog import get_proxy_logger

from .symbolication import ProfileSymbolicator

LOG = get_proxy_logger("profiler")


def save_gecko_profile(profile, filename):
    save_start = time.time()
    orjson_time = 0
    json_time = 0
    with open(filename, "wb") as f:
        if orjson is not None:
            try:
                LOG.info("Saving profile with orjson")
                orjson_start = time.time()
                data = orjson.dumps(profile)
                orjson_time = time.time() - orjson_start
                LOG.info(f"orjson.dumps() took {orjson_time:.2f}s")
                f.write(data)
            except Exception as e:
                LOG.info(f"orjson failed ({e}), falling back to json")
                json_start = time.time()
                data = json.dumps(profile).encode("utf-8")
                json_time = time.time() - json_start
                LOG.info(f"json.dumps() (fallback) took {json_time:.2f}s")
                f.write(data)
        else:
            LOG.info("Saving profile with json")
            json_start = time.time()
            data = json.dumps(profile).encode("utf-8")
            json_time = time.time() - json_start
            LOG.info(f"json.dumps() took {json_time:.2f}s")
            f.write(data)

    total_save_time = time.time() - save_start
    LOG.info("=== Save Timing Summary ===")
    if orjson_time > 0:
        LOG.info(f"orjson.dumps() time:  {orjson_time:.2f}s")
    if json_time > 0:
        LOG.info(f"json.dumps() time:    {json_time:.2f}s")
    LOG.info(f"Total save time:      {total_save_time:.2f}s")
    LOG.info("==========================")


def symbolicate_profile_json(profile_path, firefox_symbols_path):
    """
    Symbolicate a single JSON profile.
    """
    temp_dir = tempfile.mkdtemp()
    missing_symbols_zip = os.path.join(temp_dir, "missingsymbols.zip")

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

    start_time = time.time()
    try:
        load_start = time.time()
        input_file_size_mb = os.path.getsize(profile_path) / (1024 * 1024)
        LOG.info(f"Input profile file size: {input_file_size_mb:.2f} MB")
        with open(profile_path, "rb") as profile_file:
            if orjson is not None:
                try:
                    LOG.info("Loading profile with orjson")
                    orjson_start = time.time()
                    profile = orjson.loads(profile_file.read())
                    orjson_time = time.time() - orjson_start
                    LOG.info(f"orjson.loads() took {orjson_time:.2f}s")
                except Exception as e:
                    LOG.info(f"orjson failed ({e}), falling back to json")
                    profile_file.seek(0)
                    json_start = time.time()
                    profile = json.load(profile_file)
                    json_time = time.time() - json_start
                    LOG.info(f"json.load() (fallback) took {json_time:.2f}s")
            else:
                LOG.info("Loading profile with json")
                json_start = time.time()
                profile = json.load(profile_file)
                json_time = time.time() - json_start
                LOG.info(f"json.load() took {json_time:.2f}s")
        load_time = time.time() - load_start
        profile_size_mb = sys.getsizeof(profile) / (1024 * 1024)
        LOG.info(f"Total profile loading took {load_time:.2f}s")
        LOG.info(f"Loaded profile size: {profile_size_mb:.2f} MB")

        dump_start = time.time()
        symbolicator.dump_and_integrate_missing_symbols(profile, missing_symbols_zip)
        dump_time = time.time() - dump_start
        LOG.info(f"dump_and_integrate_missing_symbols took {dump_time:.2f}s")

        sym_start = time.time()
        symbolicator.symbolicate_profile(profile)
        sym_time = time.time() - sym_start
        LOG.info(f"symbolicate_profile took {sym_time:.2f}s")
        symbolicated_profile_size_mb = sys.getsizeof(profile) / (1024 * 1024)
        LOG.info(f"Symbolicated profile size: {symbolicated_profile_size_mb:.2f} MB")

        save_start = time.time()
        # Overwrite the profile in place.
        save_gecko_profile(profile, profile_path)
        save_time = time.time() - save_start
        saved_file_size_mb = os.path.getsize(profile_path) / (1024 * 1024)
        LOG.info(f"Profile saving took {save_time:.2f}s")
        LOG.info(f"Saved profile file size: {saved_file_size_mb:.2f} MB")

        total_time = time.time() - start_time
        LOG.info("=== symbolicate_profile_json Timing Summary ===")
        LOG.info(f"1. Profile loading:                 {load_time:.2f}s")
        LOG.info(f"2. dump_and_integrate_missing_symbols: {dump_time:.2f}s")
        LOG.info(f"3. symbolicate_profile:             {sym_time:.2f}s")
        LOG.info(f"4. Profile saving:                  {save_time:.2f}s")
        LOG.info(f"   ---")
        LOG.info(f"   TOTAL symbolicate_profile_json:    {total_time:.2f}s")
        LOG.info("==============================================")
    except MemoryError:
        LOG.error(
            f"Ran out of memory while trying to symbolicate profile {profile_path}"
        )
    except Exception as e:
        LOG.error("Encountered an exception during profile symbolication")
        LOG.error(e)
    finally:
        shutil.rmtree(temp_dir)
