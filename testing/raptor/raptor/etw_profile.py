# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from logger.logger import RaptorLogger

LOG = RaptorLogger(component="raptor-etw-profile")

XPERF_START_TASK = "xperf_kernel_trace_start"
XPERF_STOP_TASK = "xperf_kernel_trace_stop"
XPERF_ETL_RELATIVE = Path("xperf", "combined.etl")
XPERF_ETL_KERNEL_SESSION_RELATIVE = Path("xperf", "kernel_session.etl")
XPERF_ETL_USER_SESSION_RELATIVE = Path("xperf", "user_session.etl")
XPERF_STARTUP_TIME = 2
SCHTASKS_POLL_INTERVAL = 1
SCHTASKS_POLL_TIMEOUT = 450
SCHTASKS_RUN_TIMEOUT = 30
SAMPLY_TIMEOUT = 900


class ETWProfile:
    """Record kernel ETW traces (.etl) using xperf (via pre-configured
    scheduled tasks), then use Samply to convert and symbolicate them
    into Firefox Profiler JSON profiles. Profiler-edit filters and
    adds labelling to the profiles afterwards.

    On the Windows pool, scheduled tasks allow an
    unprivileged user to start/stop xperf kernel tracing:

        schtasks /run /tn xperf_kernel_trace_start
        schtasks /run /tn xperf_kernel_trace_stop

    The resulting ETL trace is written to:

        %USERPROFILE%\\xperf\\combined.etl.
    """

    def __init__(self, upload_dir, raptor_config, test_config):
        self.upload_dir = Path(upload_dir)
        self.test_name = test_config.get("name", "test")

        self.etl_source = Path(os.environ["USERPROFILE"]) / XPERF_ETL_RELATIVE
        self.etl_kernel_session_path = (
            Path(os.environ["USERPROFILE"]) / XPERF_ETL_KERNEL_SESSION_RELATIVE
        )
        self.etl_user_session_path = (
            Path(os.environ["USERPROFILE"]) / XPERF_ETL_USER_SESSION_RELATIVE
        )
        self.etl_dest = self.upload_dir / f"xperf-combined-{self.test_name}.etl"
        self.etl_kernel_dest = self.upload_dir / f"xperf-kernel-{self.test_name}.etl"
        self.etl_user_dest = self.upload_dir / f"xperf-user-{self.test_name}.etl"

        self.profile = self.upload_dir / f"etw-{self.test_name}-unprocessed.json.gz"

        self.running = False

        if "MOZ_AUTOMATION" in os.environ:
            moz_fetch = Path(os.environ["MOZ_FETCHES_DIR"])
            self.samply_path = moz_fetch / "samply" / "samply.exe"
            self.profiler_edit_path = (
                moz_fetch / "profiler-node-tools" / "profiler-edit.js"
            )
            self.node_path = moz_fetch / "node" / "node.exe"

        LOG.info(f"ETW profiling initialized: etl_source={self.etl_source}")

    def _run_schtask(self, task_name):
        cmd = ["schtasks", "/run", "/tn", task_name]
        LOG.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SCHTASKS_RUN_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            LOG.error(f"schtasks failed (rc={result.returncode}): {result.stderr}")
            raise RuntimeError(f"schtasks /run /tn {task_name} failed")
        LOG.info(f"schtasks output: {result.stdout.strip()}")

    def _wait_for_combined_etl(self, timeout=SCHTASKS_POLL_TIMEOUT):
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
        time.sleep(XPERF_STARTUP_TIME)
        LOG.info("xperf kernel trace started")

    def stop(self):
        if not self.running:
            LOG.warning("No active xperf trace session")
            return
        self._run_schtask(XPERF_STOP_TASK)
        self.running = False
        LOG.info("xperf kernel trace stopped")

    def _edit_profile(
        self,
        input_profile=None,
        output_profile=None,
        name=None,
        labels=False,
        compact=False,
        profiler_edit_args=None,
    ):
        if input_profile is None:
            input_profile = self.profile

        if output_profile is None:
            LOG.error("No output profile path specified.")
            return None

        input_profile = Path(input_profile)
        output_profile = Path(output_profile)

        profiler_edit_cmd = [
            str(self.node_path),
            str(self.profiler_edit_path),
            "-i",
            str(input_profile),
            "-o",
            str(output_profile),
        ]

        if profiler_edit_args is None:
            profiler_edit_args = []

        if labels:
            browser_labels_toml = (
                Path(__file__).resolve().parent / "browser_labels.toml"
            )
            profiler_edit_args.extend([
                "--insert-label-frames",
                str(browser_labels_toml),
            ])

        if compact:
            profiler_edit_args.append(
                "--only-keep-threads-with-markers-matching=-async,-sync"
            )
            profiler_edit_args.append("--merge-non-overlapping-threads-by-name")

        if name:
            profiler_edit_args.extend(["--set-name", name])

        profiler_edit_cmd.extend(profiler_edit_args)

        LOG.info(f"Running: {' '.join(profiler_edit_cmd)}")
        result = subprocess.run(
            profiler_edit_cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            LOG.info(f"profiler-edit stdout: {line}")
        for line in result.stderr.splitlines():
            LOG.info(f"profiler-edit stderr: {line}")
        if result.returncode != 0:
            LOG.error(f"profiler-edit exited with code {result.returncode}")
            return None
        if not output_profile.exists():
            LOG.error(f"profiler-edit did not produce a profile at {output_profile}")
            return None

        return output_profile

    def upload(self):
        # Process an upload a raw profile
        output_profile_path = (
            self.upload_dir / "profile_sp3_etw_raw_all_processes.json.gz"
        )
        raw_profile = self._edit_profile(
            output_profile=output_profile_path, name="Sp3 20x (all processes)"
        )
        if raw_profile is None:
            LOG.error(f"Failed to create {output_profile_path.name}")
        else:
            size = output_profile_path.stat().st_size
            LOG.info(f"Created {output_profile_path.name}: {size} bytes")

        # Process and upload a raw profile with labels
        output_profile_path = self.upload_dir / "profile_sp3_etw_all_processes.json.gz"
        labelled_profile = self._edit_profile(
            output_profile=output_profile_path,
            name="Sp3 20x (with labels, all processes)",
            labels=True,
        )
        if labelled_profile is None:
            LOG.error(f"Failed to create {output_profile_path.name}")
        else:
            size = output_profile_path.stat().st_size
            LOG.info(f"Created {output_profile_path.name}: {size} bytes")

        # Process and upload a compact, one thread profile
        output_profile_path = self.upload_dir / "profile_sp3_etw_compact.json.gz"
        compact_profile = self._edit_profile(
            output_profile=output_profile_path,
            name="Sp3 20x (with labels, combined main threads)",
            labels=True,
            compact=True,
        )
        if compact_profile is None:
            LOG.error(f"Failed to create {output_profile_path.name}")
        else:
            size = output_profile_path.stat().st_size
            LOG.info(f"Created {output_profile_path.name}: {size} bytes")

        self.post_process_success = raw_profile and labelled_profile and compact_profile

        # Wait for xperf to finishing merging the kernel and user traces
        self._wait_for_combined_etl()

        # Upload combined etl
        if self.etl_source.exists():
            shutil.copy2(self.etl_source, self.etl_dest)
            LOG.info(f"ETL archived to: {self.etl_dest}")

        # Upload kernel session trace
        if self.etl_kernel_session_path.exists():
            shutil.copy2(self.etl_kernel_session_path, self.etl_kernel_dest)
            LOG.info(f"ETL archived to: {self.etl_kernel_dest}")

        # Upload user session trace
        if self.etl_user_session_path.exists():
            shutil.copy2(self.etl_user_session_path, self.etl_user_dest)
            LOG.info(f"ETL archived to: {self.etl_user_dest}")

    def symbolicate(self):
        if not self.etl_kernel_session_path.exists():
            LOG.error(f"Cannot find kernel ETL file: {self.etl_kernel_session_path}")
            return

        if not self.etl_user_session_path.exists():
            LOG.error(f"Cannot find user ETL file: {self.etl_user_session_path}")
            return

        # Extracting crashreporter symbols
        moz_fetch = Path(os.environ["MOZ_FETCHES_DIR"])
        self.breakpad_symbol_dir = moz_fetch / "target.crashreporter-symbols"
        breakpad_symbol_zip = Path(f"{self.breakpad_symbol_dir}.zip")

        if not breakpad_symbol_zip.exists():
            LOG.error(f"Breakpad symbol zip not found: {breakpad_symbol_zip}")
            return

        LOG.info(f"Unzipping {breakpad_symbol_zip}")
        with zipfile.ZipFile(breakpad_symbol_zip, "r") as zipf:
            zipf.extractall(self.breakpad_symbol_dir)

        # Produce symbolicated profiles with samply
        samply_cmd = [
            str(self.samply_path),
            "import",
            str(self.etl_kernel_session_path),
            str(self.etl_user_session_path),
            "--save-only",
            "-o",
            str(self.profile),
            "--presymbolicate",
            "--main-thread-only",
            "--breakpad-symbol-dir",
            str(self.breakpad_symbol_dir),
            "--breakpad-symbol-server",
            "https://symbols.mozilla.org/",
        ]
        LOG.info(f"Running: {' '.join(samply_cmd)}")
        result = subprocess.run(
            samply_cmd,
            capture_output=True,
            text=True,
            timeout=SAMPLY_TIMEOUT,
            check=False,
        )
        for line in result.stdout.splitlines():
            LOG.info(f"samply stdout: {line}")
        for line in result.stderr.splitlines():
            LOG.info(f"samply stderr: {line}")
        if result.returncode != 0:
            LOG.error(f"samply exited with code {result.returncode}")
            return False
        if not self.profile.exists():
            LOG.error(f"samply did not produce a profile at {self.profile}")
            return False

        size = self.profile.stat().st_size
        LOG.info(f"Profile converted: {self.profile} ({size} bytes)")

        return True

    def clean(self):
        if self.etl_source.exists():
            self.etl_source.unlink()
            LOG.info("Cleaned up source ETL file")

        if self.breakpad_symbol_dir and self.breakpad_symbol_dir.exists():
            shutil.rmtree(self.breakpad_symbol_dir)

        # If profiler-edit fails, upload the profile produced by Samply for debugging
        if self.profile.exists() and self.post_process_success:
            self.profile.unlink()
