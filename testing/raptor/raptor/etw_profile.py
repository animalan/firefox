# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import shutil
import subprocess
import time
from pathlib import Path

from logger.logger import RaptorLogger

LOG = RaptorLogger(component="raptor-etw-profile")

XPERF_START_TASK = "xperf_kernel_trace_start"
XPERF_STOP_TASK = "xperf_kernel_trace_stop"
XPERF_ETL_RELATIVE = Path("xperf", "combined.etl")
SCHTASKS_POLL_INTERVAL = 1
SCHTASKS_POLL_TIMEOUT = 30


class ETWProfile:
    """Collect xperf kernel ETW traces via pre-configured scheduled tasks,
    then convert to Firefox Profiler format with samply.

    On the win11-64-24h2-hw-perf-debug pool, scheduled tasks allow an
    unprivileged user to start/stop xperf kernel tracing:
      schtasks /run /tn xperf_kernel_trace_start
      schtasks /run /tn xperf_kernel_trace_stop
    The resulting ETL is written to %USERPROFILE%\\xperf\\combined.etl.
    After collection, samply converts the ETL to a Firefox Profiler JSON.
    """

    def __init__(self, upload_dir, raptor_config, test_config):
        upload_dir = Path(upload_dir)
        test_name = test_config["name"]

        self.etl_source = Path(os.environ["USERPROFILE"]) / XPERF_ETL_RELATIVE
        self.etl_dest = upload_dir / f"xperf-kernel-{test_name}.etl"
        self.profile_path = upload_dir / f"etw-profile-{test_name}.json"
        self.running = False

        if os.environ.get("MOZ_FETCHES_DIR"):
            self.samply_path = (
                Path(os.environ["MOZ_FETCHES_DIR"]) / "samply" / "samply.exe"
            )
        else:
            self.samply_path = Path("samply")

        LOG.info(f"ETW profiling initialized: etl_source={self.etl_source}")

    def _run_schtask(self, task_name):
        cmd = ["schtasks", "/run", "/tn", task_name]
        LOG.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            LOG.error(f"schtasks failed (rc={result.returncode}): {result.stderr}")
            raise RuntimeError(f"schtasks /run /tn {task_name} failed")
        LOG.info(f"schtasks output: {result.stdout.strip()}")

    def _wait_for_etl(self, timeout=SCHTASKS_POLL_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.etl_source.exists():
                time.sleep(1)
                size = self.etl_source.stat().st_size
                if size > 0:
                    LOG.info(f"ETL file ready: {self.etl_source} ({size} bytes)")
                    return True
            time.sleep(SCHTASKS_POLL_INTERVAL)
        LOG.error(f"ETL file not found after {timeout}s: {self.etl_source}")
        return False

    def start(self):
        if self.etl_source.exists():
            self.etl_source.unlink()
            LOG.info("Removed stale ETL file")

        self._run_schtask(XPERF_START_TASK)
        self.running = True
        time.sleep(2)
        LOG.info("xperf kernel trace started")

    def stop(self):
        if not self.running:
            LOG.warning("No active xperf trace session")
            return

        self._run_schtask(XPERF_STOP_TASK)
        self.running = False
        self._wait_for_etl()

    def symbolicate(self):
        import zipfile

        if not self.etl_source.exists():
            LOG.warning("No ETL file to archive")
            return

        shutil.copy2(self.etl_source, self.etl_dest)
        LOG.info(f"ETL archived to: {self.etl_dest}")

        moz_fetch = Path(os.environ["MOZ_FETCHES_DIR"])
        breakpad_symbol_dir = moz_fetch / "target.crashreporter-symbols"
        breakpad_symbol_zip = breakpad_symbol_dir.with_suffix(".zip")

        # Extracting crashreporter symbols
        if breakpad_symbol_zip.exists():
            with zipfile.ZipFile(breakpad_symbol_zip, "r") as zipf:
                zipf.extractall(breakpad_symbol_dir)

        # Produce symbolicated profiles with samply
        result = subprocess.run(
            [
                str(self.samply_path),
                "import",
                str(self.etl_dest),
                "--save-only",
                "-o",
                str(self.profile_path),
                "--presymbolicate",
                "--breakpad-symbol-dir",
                str(breakpad_symbol_dir),
                "--breakpad-symbol-server",
                "https://symbols.mozilla.org/",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

        for line in result.stdout.splitlines():
            LOG.info(f"samply stdout: {line}")
        for line in result.stderr.splitlines():
            LOG.info(f"samply stderr: {line}")
        if result.returncode != 0:
            LOG.error(f"samply exited with code {result.returncode}")
            return False
        if not self.profile_path.exists():
            LOG.error(f"samply did not produce a profile at {self.profile_path}")
            return False

        size = self.profile_path.stat().st_size
        LOG.info(f"Profile converted: {self.profile_path} ({size} bytes)")
        return True

    def clean(self):
        if self.etl_source.exists():
            self.etl_source.unlink()
            LOG.info("Cleaned up source ETL file")
