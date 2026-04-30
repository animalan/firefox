/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

package org.mozilla.fenix.components.ipprotection

import android.content.SharedPreferences
import androidx.core.content.edit
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.runTest
import mozilla.components.support.base.feature.LifecycleAwareFeature
import mozilla.components.support.test.robolectric.testContext
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.mozilla.fenix.R
import org.mozilla.fenix.helpers.lifecycle.TestLifecycleOwner
import org.mozilla.fenix.nimbus.FxNimbus
import org.mozilla.fenix.nimbus.IpProtection
import org.mozilla.fenix.utils.Settings

@RunWith(AndroidJUnit4::class)
class IPProtectionFeatureIntegrationTest {
    private lateinit var prefs: SharedPreferences
    private lateinit var prefKey: String
    private lateinit var owner: TestLifecycleOwner

    @Before
    fun setup() {
        prefs = Settings(testContext).preferences
        prefKey = testContext.getString(R.string.pref_key_enable_ip_protection)
        owner = TestLifecycleOwner()
        FxNimbus.features.ipProtection.withCachedValue(IpProtection(enabled = false))
    }

    @Test
    fun `GIVEN pref is true and nimbus is disabled WHEN start is called THEN feature is started`() = runTest {
        prefs.edit { putBoolean(prefKey, true) }
        var result = false
        val feature = object : LifecycleAwareFeature {
            override fun start() { result = true }
            override fun stop() = Unit
        }

        createIntegration(feature, testScheduler).start()

        assertTrue(result)
    }

    @Test
    fun `GIVEN pref is false and nimbus is enabled WHEN start is called THEN feature is started`() = runTest {
        FxNimbus.features.ipProtection.withCachedValue(IpProtection(enabled = true))
        var result = false
        val feature = object : LifecycleAwareFeature {
            override fun start() { result = true }
            override fun stop() = Unit
        }

        createIntegration(feature, testScheduler).start()

        assertTrue(result)
    }

    @Test
    fun `GIVEN pref is true and nimbus is enabled WHEN start is called THEN feature is started`() = runTest {
        FxNimbus.features.ipProtection.withCachedValue(IpProtection(enabled = true))
        prefs.edit { putBoolean(prefKey, true) }
        var result = false
        val feature = object : LifecycleAwareFeature {
            override fun start() { result = true }
            override fun stop() = Unit
        }

        createIntegration(feature, testScheduler).start()

        assertTrue(result)
    }

    @Test
    fun `GIVEN pref is false and nimbus is disabled WHEN start is called THEN feature is stopped`() = runTest {
        var result = false
        val feature = object : LifecycleAwareFeature {
            override fun start() = Unit
            override fun stop() { result = true }
        }

        createIntegration(feature, testScheduler).start()

        assertTrue(result)
    }

    @Test
    fun `WHEN pref toggles from false to true THEN feature is started`() = runTest {
        var result = false
        val feature = object : LifecycleAwareFeature {
            override fun start() { result = true }
            override fun stop() = Unit
        }

        createIntegration(feature, testScheduler).start()

        assertFalse(result)

        prefs.edit { putBoolean(prefKey, true) }

        assertTrue(result)
    }

    @Test
    fun `WHEN pref toggles from true to false THEN feature is stopped`() = runTest {
        prefs.edit { putBoolean(prefKey, true) }
        var result = false
        val feature = object : LifecycleAwareFeature {
            override fun start() = Unit
            override fun stop() { result = true }
        }

        createIntegration(feature, testScheduler).start()

        assertFalse(result)

        prefs.edit { putBoolean(prefKey, false) }

        assertTrue(result)
    }

    private fun createIntegration(
        feature: LifecycleAwareFeature,
        testScheduler: TestCoroutineScheduler,
    ) = IPProtectionFeatureIntegration(
        feature = feature,
        pref = prefs,
        prefKey = prefKey,
        lifecycleOwner = owner,
        mainContext = testScheduler,
    )
}
