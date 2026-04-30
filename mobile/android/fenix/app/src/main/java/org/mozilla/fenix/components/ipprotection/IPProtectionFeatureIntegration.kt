/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

package org.mozilla.fenix.components.ipprotection

import android.content.SharedPreferences
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import mozilla.components.support.base.feature.LifecycleAwareFeature
import org.mozilla.fenix.nimbus.FxNimbus
import org.mozilla.fenix.onboarding.flowScopedBooleanPreference
import kotlin.coroutines.CoroutineContext

/**
 * A wrapper class to integrate IP protection feature into fenix.
 *
 * @param feature The [LifecycleAwareFeature] to wrap.
 * @param pref The [SharedPreferences] instance to observe for changes.
 * @param prefKey The key in [SharedPreferences] representing the secret settings feature flag.
 * @param lifecycleOwner The lifecycle owner driving the preference observer.
 * @param mainContext The coroutine context used to register the preference listener.
 */
class IPProtectionFeatureIntegration(
    private val feature: LifecycleAwareFeature,
    private val pref: SharedPreferences,
    private val prefKey: String,
    private val lifecycleOwner: LifecycleOwner,
    private val mainContext: CoroutineContext = Dispatchers.Main,
) {

    /**
     * Starts the [feature] if conditions are met.
     */
    fun start() {
        lifecycleOwner.lifecycleScope.launch(mainContext) {
            pref.flowScopedBooleanPreference(
                lifecycleOwner,
                mainContext,
                prefKey,
                false,
            )
                .distinctUntilChanged()
                .collect { isEnabled ->
                    if (isEnabled || FxNimbus.features.ipProtection.value().enabled) {
                        feature.start()
                    } else {
                        feature.stop()
                    }
                }
        }
    }
}
