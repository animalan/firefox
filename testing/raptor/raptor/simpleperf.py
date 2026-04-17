# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Module to handle Simpleperf profiling.
"""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from logger.logger import RaptorLogger
from mozdevice import ADBDeviceFactory
from raptor_profiling import RaptorProfiling

LOG = RaptorLogger(component="raptor-simpleperf")


class SimpleperfBinaryNotFoundError(Exception):
    pass


class SimpleperfAlreadyRunningError(Exception):
    pass


class SimpleperfNotRunningError(Exception):
    pass


SIMPLEPERF_OPTIONS = (
    "--call-graph fp --duration 1000 -f 1000 --trace-offcpu -e cpu-clock -a"
)

class SimpleperfProfile(RaptorProfiling):
    """
    Handle Simpleperf profiling.

    This allows us to process Simpleperf profiles in Raptor.
    """

    def __init__(self, upload_dir, raptor_config, test_config):
        super().__init__(upload_dir, raptor_config, test_config)

        self.upload_dir = Path(upload_dir)

        self.breakpad_symbol_dir = None
        self.samply_path = None
        self.device = ADBDeviceFactory()
        self.profiler_process = None
        self.use_app_profiler = False

        if "MOZ_AUTOMATION" in os.environ:
            moz_fetch = Path(os.environ["MOZ_FETCHES_DIR"])
            self.breakpad_symbol_dir = moz_fetch / "target.crashreporter-symbols"
            self.samply_path = moz_fetch / "samply" / "samply"
            simpleperf_dir = moz_fetch / "android-simpleperf"
        else:
            # ~/locally/target.crashreporter-symbols
            objdir = os.environ.get("MOZ_DEVELOPER_OBJ_DIR")
            if objdir:
                symbol_dir = Path(objdir, "dist", "crashreporter-symbols")
                self.breakpad_symbol_dir = symbol_dir if symbol_dir.exists() else None
            else:
                self.breakpad_symbol_dir = None

            # ~/samply/target/release/samply
            self.samply_path = Path("samply")

            ndk_dirs = sorted(Path.home().glob(".mozbuild/android-ndk-r*/simpleperf"))
            if not ndk_dirs:
                raise SimpleperfBinaryNotFoundError(
                    "Could not find Android NDK in ~/.mozbuild"
                )
            simpleperf_dir = ndk_dirs[-1]
        self.dest_dir = (
            self.upload_dir
            / "browsertime-results"
            / self.test_config.get("name", "simpleperf")
        )

        self.simpleperf_binary = (
            simpleperf_dir / "bin" / "android" / "arm64" / "simpleperf"
        )
        self.app_profiler = simpleperf_dir / "app_profiler.py"

    def _push_simpleperf_binary(self):
        self.device.shell("rm -f /data/local/tmp/simpleperf /data/local/tmp/perf.data")
        self.device.push(str(self.simpleperf_binary), "/data/local/tmp")
        self.device.shell("chmod a+x /data/local/tmp/simpleperf")

    def start(self, simpleperf_options=None, use_app_profiler=False):
        LOG.info("Starting Simpleperf")
        if self.profiler_process:
            raise SimpleperfAlreadyRunningError("simpleperf already running")

        self.device.shell("kill -9 $(pgrep simpleperf) 2>/dev/null; true")

        if simpleperf_options is None:
            simpleperf_options = SIMPLEPERF_OPTIONS

        self.use_app_profiler = use_app_profiler

        if use_app_profiler:
            if not self.app_profiler.exists():
                raise SimpleperfBinaryNotFoundError(
                    f"app_profiler.py not found at {self.app_profiler}"
                )

            package_name = self.raptor_config.get("binary")

            cmd = [
                str(self.app_profiler),
                "-p",
                str(package_name),
                "-r",
                simpleperf_options,
                "-o",
                "/data/local/tmp/perf.data",
            ]

        else:
            if not self.simpleperf_binary.exists():
                raise SimpleperfBinaryNotFoundError(
                    f"simpleperf binary not found at {self.simpleperf_binary}"
                )

            self._push_simpleperf_binary()

            record_cmd = f"/data/local/tmp/simpleperf record {simpleperf_options} -o /data/local/tmp/perf.data"
            cmd = ["adb", "shell", "su", "-c", record_cmd]

        self.profiler_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        LOG.info("Started Simpleperf")

    def stop(self):
        LOG.info("Stopping Simpleperf")
        if not self.profiler_process:
            raise SimpleperfNotRunningError("no profiler process found")

        sigint = -2
        self.device.shell(f"kill {sigint} $(pgrep simpleperf)")

        for line in self.profiler_process.stdout:
            LOG.info(f"simpleperf: {line.decode().strip()}")
        self.profiler_process.wait()
        LOG.info(f"simpleperf exited with code {self.profiler_process.returncode}")

        self.profiler_process = None

        profile_path = self.dest_dir / "perf.data"
        self.device.pull("/data/local/tmp/perf.data", str(profile_path))
        self.device.shell("rm -f /data/local/tmp/perf.data")

        if self.use_app_profiler:
            self._move_binary_cache()

        LOG.info("Stopped Simpleperf")

    def _move_binary_cache(self):
        binary_cache = Path("binary_cache")
        if binary_cache.exists():
            shutil.move(str(binary_cache), str(self.dest_dir / "binary_cache"))
        else:
            LOG.info("binary_cache not found, skipping")

    def _pull_jit_marker_files(self):
        package_name = self.raptor_config.get("binary")
        if not package_name:
            LOG.warning("Package name not set. Skipping JIT/marker file pull.")
            return
        files_dir = f"/storage/emulated/0/Android/data/{package_name}/files"
        try:
            device_files = self.device.shell_output(
                f"ls {files_dir}/jit-*.dump {files_dir}/marker-*.txt 2>/dev/null"
            )
            for file in device_files.splitlines():
                file = file.strip()
                if not file:
                    continue
                file_name = file.split("/")[-1]
                self.device.pull(file, str(self.dest_dir / file_name), timeout=15)
        except Exception as e:
            LOG.error(f"Failed to pull JIT/marker files: {e}")

    def symbolicate(self):
        if not self.breakpad_symbol_dir:
            LOG.info("symbols directory not set, skipping symbolication")
            return

        if not self.samply_path:
            LOG.info("samply not set, skipping symbolication")
            return

        if not self.samply_path.exists():
            LOG.info("samply not found, skipping symbolication")
            return

        symbol_zip = Path(f"{self.breakpad_symbol_dir}.zip")
        if "MOZ_AUTOMATION" in os.environ and symbol_zip.exists():
            with zipfile.ZipFile(symbol_zip, "r") as zipf:
                zipf.extractall(self.breakpad_symbol_dir)

        if not self.breakpad_symbol_dir.exists():
            LOG.info("symbols directory not found, skipping symbolication")
            return

        # Find all perf.data files
        perf_files = list(self.dest_dir.rglob("perf.data"))

        if not perf_files:
            LOG.error(f"perf.data not found at {self.dest_dir}, skipping symbolication")
            return

        if "MOZ_AUTOMATION" in os.environ:
            profile_archive = Path(
                self.upload_dir, f"profile_{self.test_config['name']}.zip"
            )

            try:
                mode = zipfile.ZIP_DEFLATED
            except NameError:
                mode = zipfile.ZIP_STORED

        for perf_file in perf_files:
            profile = perf_file.parent / f"{perf_file.parent.name}.json.gz"

            try:
                result = subprocess.run(
                    [
                        str(self.samply_path),
                        "import",
                        str(perf_file),
                        "--save-only",
                        "-o",
                        str(profile),
                        "--presymbolicate",
                        "--breakpad-symbol-dir",
                        str(self.breakpad_symbol_dir),
                        "--breakpad-symbol-server",
                        "https://symbols.mozilla.org/",
                        "--aux-file-dir",
                        str(perf_file.parent),
                        "--name",
                        self.raptor_config.get("binary", "org.mozilla.fenix"),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                for line in result.stdout.splitlines():
                    LOG.info(f"samply stdout: {line}")
                for line in result.stderr.splitlines():
                    LOG.info(f"samply stderr: {line}")
                if result.returncode != 0:
                    LOG.error(f"samply exited with code {result.returncode}")
                if not profile.exists():
                    LOG.error(f"samply did not produce a profile at {profile}")
                else:
                    LOG.info(
                        f"Profile converted: {profile} ({profile.stat().st_size} bytes)"
                    )

                    if "MOZ_AUTOMATION" in os.environ:
                        with zipfile.ZipFile(profile_archive, "a", mode) as zipf:
                            path_in_zip = f"simpleperf/{profile.name}"
                            LOG.info(
                                f"Adding {profile.name} to {profile_archive} as {path_in_zip}"
                            )
                            zipf.write(profile, arcname=path_in_zip)
                            profile.unlink(missing_ok=True)
            finally:
                perf_file.unlink(missing_ok=True)
                if perf_file.parent.exists():
                    for marker in perf_file.parent.glob("marker-*.txt"):
                        marker.unlink(missing_ok=True)
                    for jitdump in perf_file.parent.rglob("jit-*.dump"):
                        jitdump.unlink(missing_ok=True)

        if "MOZ_AUTOMATION" in os.environ:
            if profile_archive.exists():
                LOG.info(
                    f"Profiles archived to: {profile_archive} ({profile_archive.stat().st_size} bytes)"
                )
            elif profile_archive:
                LOG.error(f"Failed to archive profiles to {profile_archive}")

    def clean(self):
        if self.breakpad_symbol_dir and self.breakpad_symbol_dir.exists():
            shutil.rmtree(self.breakpad_symbol_dir)
