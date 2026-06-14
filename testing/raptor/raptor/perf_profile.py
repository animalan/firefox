# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import platform
import shutil
import signal
import subprocess
import zipfile
from pathlib import Path

from logger.logger import RaptorLogger
from raptor_profiling import RaptorProfiling

LOG = RaptorLogger(component="raptor-perf")


class PerfProfile(RaptorProfiling):
    """Collect system-wide perf traces and convert to Firefox Profiler format.

    Runs `perf record -a -g` in the background during the test, then uses
    samply to convert the resulting perf.data to Firefox Profiler JSON.
    """

    def _get_build_symbols(self):
        # Locate symbol directory for breakpad symbolication during profile conversion.
        if os.environ.get("MOZ_AUTOMATION"):
            # CI: Extract symbols from fetched archive to temp directory
            symbol_extract_dir = self.temp_dir / "symbols"
            moz_fetches = Path(os.environ["MOZ_FETCHES_DIR"])
            symbol_zip = moz_fetches / "target.crashreporter-symbols.zip"
            if not symbol_zip.exists():
                LOG.warning(f"Symbol zip not found at {symbol_zip}")
                return None
            LOG.info(f"Extracting {symbol_zip}")
            try:
                with zipfile.ZipFile(symbol_zip, "r") as zipf:
                    zipf.extractall(symbol_extract_dir)
            except Exception as e:
                LOG.warning(f"Failed to extract symbols: {e}")
                return None
            return symbol_extract_dir
        # Local development: check CLI argument first, then environment variable
        elif (
            self.raptor_config.get("symbols_path")
            and Path(self.raptor_config.get("symbols_path")).exists()
        ):
            return Path(self.raptor_config.get("symbols_path"))
        elif "MOZ_DEVELOPER_OBJ_DIR" in os.environ:
            sym_dir = Path(
                os.environ["MOZ_DEVELOPER_OBJ_DIR"], "dist", "crashreporter-symbols"
            )
            if sym_dir.exists():
                return sym_dir
            LOG.warning(f"Symbol directory not found at {sym_dir}")
            return None
        else:
            LOG.warning(
                "No symbol directory found. Set --symbolsPath or MOZ_DEVELOPER_OBJ_DIR"
            )
            return None

    def __init__(self, upload_dir, raptor_config, test_config):
        if platform.system() != "Linux":
            raise RuntimeError("Perf profiling is only supported on Linux")

        super().__init__(upload_dir, raptor_config, test_config)

        # Initialize output paths and profiling parameters
        self.test_name = test_config.get("name", "test")
        self.upload_dir = Path(self.upload_dir)
        self.profile = self.upload_dir / f"profile_{self.test_name}.json"
        self.rate = 100
        self.perf_data_path = self.upload_dir / f"perf-{self.test_name}.data"
        self.profile_archive = self.upload_dir / f"profile_{self.test_name}.zip"
        self._proc = None
        self.running = False
        self.temp_dir = Path(self.temp_profile_dir)

        # Locate samply tool from toolchain (CI or local MozBuild)
        if "MOZ_AUTOMATION" in os.environ:
            toolchain_dir = Path(os.environ.get("MOZ_FETCHES_DIR", ""))
            self.samply_path = toolchain_dir / "samply" / "samply"
        else:
            toolchain_dir = Path(
                os.environ.get("MOZBUILD_STATE_PATH", Path.home() / ".mozbuild")
            )
            self.samply_path = toolchain_dir / "samply" / "samply"

        # Verify samply is available before proceeding
        if not self.samply_path.exists():
            raise FileNotFoundError(
                f"samply not found at {self.samply_path}. Run ./mach bootstrap to install."
            )

        # Locate symbol directory for later profile symbolication
        self.breakpad_symbol_dir = self._get_build_symbols()

        if self.breakpad_symbol_dir and self.breakpad_symbol_dir.exists():
            LOG.info(f"Symbol directory: {self.breakpad_symbol_dir}")
            os.environ["MOZ_CRASHREPORTER_NO_REPORT"] = "1"
            if self.raptor_config.get("symbols_path"):
                os.environ["MOZ_CRASHREPORTER"] = "1"
            else:
                os.environ["MOZ_CRASHREPORTER_DISABLE"] = "1"
        else:
            LOG.info(
                "Symbol directory not found. Skipping crash reporter configuration."
            )

        # Firefox Profiling settings may already be set upstream,
        # so only set them if they don't already exist.
        for key, val in {
            "IONPERF": "func",
            "MOZ_USE_PERFORMANCE_MARKER_FILE": "1",
            "MOZ_DISABLE_CONTENT_SANDBOX": "1",
            "JIT_OPTION_enableICFramePointers": "true",
            "JIT_OPTION_onlyInlineSelfHosted": "true",
            "JIT_OPTION_emitInterpreterEntryTrampoline": "true",
        }.items():
            raptor_config.setdefault("environment", {}).setdefault(key, val)

        LOG.info("Initialization successful.")
        for key, value in self.__dict__.items():
            LOG.info(f"{key}: {value}")
        for key, value in raptor_config.items():
            LOG.info(f"{key}: {value}")
        for key, value in test_config.items():
            LOG.info(f"{key}: {value}")

    def start(self):
        LOG.info("=== Starting perf profiling ===")
        LOG.info(f"perf_data_path: {self.perf_data_path}")
        LOG.info(f"rate: {self.rate}")
        LOG.info(f"samply_path: {self.samply_path}")

        # Clean up any stale perf.data from previous runs
        if self.perf_data_path.exists():
            LOG.info(f"Found stale perf.data, removing: {self.perf_data_path}")
            self.perf_data_path.unlink()
            LOG.info("Removed stale perf.data file")
        else:
            LOG.info(f"No stale perf.data found at {self.perf_data_path}")

        # Pre-create perf.data file so root-owned perf process can write to it
        # without ownership issues, keeping it readable for samply conversion later
        LOG.info(f"Creating perf.data file with mode 0o644 at {self.perf_data_path}")
        self.perf_data_path.touch(mode=0o644)
        LOG.info("File created successfully")

        # Build and start perf record command with system-wide profiling
        cmd = [
            "sudo",
            "-n",  # Run non-interactively without password prompt
            "perf",
            "record",  # Start perf record profiler
            "-a",  # Profile all CPUs system-wide
            "-g",  # Record call graphs (stack traces)
            "-F",
            str(self.rate),  # Sampling frequency in Hz
            "-o",
            str(self.perf_data_path),  # Output file path
        ]
        LOG.info(f"Constructed perf command: {' '.join(cmd)}")
        LOG.info("Starting perf record process")
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        self.running = True
        return True

    def stop(self):
        # Check if perf process is still running
        if not self.running or self._proc is None:
            LOG.warning("No active perf record session")
            return

        # Gracefully stop perf record, with fallback to force kill if needed
        LOG.info(f"Stopping perf record (pid={self._proc.pid})")
        try:
            LOG.info(f"Sending SIGINT signal to process {self._proc.pid}")
            self._proc.send_signal(signal.SIGINT)
            LOG.info("Waiting for process to exit (timeout=60s)")
            self._proc.wait(timeout=60)
            LOG.info("Process exited gracefully")
        except subprocess.TimeoutExpired:
            # Graceful shutdown failed, force kill the process
            LOG.error("perf record did not stop after SIGINT, force killing")
            LOG.info(f"Killing process {self._proc.pid}")
            self._proc.kill()
            try:
                LOG.info("Waiting for killed process to exit (timeout=10s)")
                self._proc.wait(timeout=10)
                LOG.info("Killed process exited")
            except subprocess.TimeoutExpired:
                LOG.error("sudo process did not exit, giving up")
        finally:
            self.running = False

        # Verify perf.data file was written successfully
        if not self.perf_data_path.exists():
            LOG.error(f"perf.data not found after stop: {self.perf_data_path}")
            return

        LOG.info(
            f"perf.data successfully written: {self.perf_data_path} ({self.perf_data_path.stat().st_size} bytes)"
        )
        return True

    def symbolicate(self):
        LOG.info("=== Symbolicating perf profile ===")
        LOG.info(f"perf_data_path: {self.perf_data_path}")
        LOG.info(f"samply_path: {self.samply_path}")
        LOG.info(f"breakpad_symbol_dir: {self.breakpad_symbol_dir}")

        # Verify perf.data file exists before processing
        LOG.info(f"Checking if perf.data exists at {self.perf_data_path}")
        if not self.perf_data_path.exists():
            LOG.warning("No perf.data to symbolicate")
            return
        LOG.info("perf.data exists")

        # Convert perf.data to Firefox Profiler format using samply
        # Output to temp directory first, move to upload directory if successful
        temp_profile_path = self.temp_dir / f"profile_{self.test_name}.json"
        cmd = [
            str(self.samply_path),
            "import",  # Import perf.data format
            str(self.perf_data_path),  # Input perf.data file
            "--save-only",
            "-o",
            str(temp_profile_path),
            "--presymbolicate",
            "--reuse-threads",
            "--breakpad-symbol-server",
            "https://symbols.mozilla.org/",
        ]
        if self.breakpad_symbol_dir and self.breakpad_symbol_dir.exists():
            LOG.info(f"Adding breakpad-symbol-dir: {self.breakpad_symbol_dir}")
            cmd += [
                "--breakpad-symbol-dir",
                str(self.breakpad_symbol_dir),
            ]  # Local symbol directory
        else:
            LOG.info(
                f"Not adding breakpad-symbol-dir (exists: {self.breakpad_symbol_dir.exists() if self.breakpad_symbol_dir else False})"
            )

        LOG.info(f"Running symbolication command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        LOG.info(f"samply process returned with code: {result.returncode}")
        for line in result.stdout.splitlines():
            LOG.info(f"samply stdout: {line}")
        for line in result.stderr.splitlines():
            LOG.info(f"samply stderr: {line}")
        if result.returncode != 0:
            LOG.error(f"samply failed (rc={result.returncode})")
            return

        LOG.info(f"Checking if temp_profile_path exists: {temp_profile_path}")
        if not temp_profile_path.exists():
            LOG.error("samply did not produce symbolicated profile")
            return

        LOG.info(
            f"Profile successfully converted: {temp_profile_path} ({temp_profile_path.stat().st_size} bytes)"
        )
        LOG.info(f"Moving profile from {temp_profile_path} to {self.profile}")
        temp_profile_path.replace(self.profile)
        LOG.info(
            f"Profile moved successfully: {self.profile} ({self.profile.stat().st_size} bytes)"
        )

    def archive(self):
        if not self.profile.exists():
            LOG.error(f"Profile does not exist: {self.profile}")
            return False

        archive_path = self.upload_dir / f"profile_{self.test_name}.zip"
        try:
            mode = zipfile.ZIP_DEFLATED
        except NameError:
            mode = zipfile.ZIP_STORED
        try:
            with zipfile.ZipFile(archive_path, "w", mode) as zipf:
                LOG.info(f"Adding {self.profile} to archive as {self.profile.name}")
                zipf.write(self.profile, arcname=self.profile.name)
            LOG.info(
                f"Archive created successfully: {archive_path} ({archive_path.stat().st_size} bytes)"
            )
            self.profile.unlink()
            self.profile = None
            return archive_path
        except Exception as e:
            LOG.error(f"Failed to create archive: {e}")
            return None

    def clean(self):
        # Clean up temporary directory to free disk space
        if self.temp_dir.exists():
            LOG.info(f"Removing temp directory: {self.temp_dir}")
            shutil.rmtree(self.temp_dir)
