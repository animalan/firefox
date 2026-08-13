/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

add_task(async function test_intentional_hang() {
  info("Starting intentional hang test to trigger timeout profiling...");

  // This will never resolve, triggering the timeout handler after ~360s
  // The timeout handler will capture a profile using signals
  await new Promise(() => {
    // Intentionally never resolve to trigger hang detection
  });
});
