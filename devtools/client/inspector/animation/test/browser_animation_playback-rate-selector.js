/* Any copyright is dedicated to the Public Domain.
   http://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

// Test for the PlaybackRateSelector component

add_task(async function () {
  await addTab(URL_ROOT + "doc_custom_playback_rate.html");
  const { animationInspector, panel } = await openAnimationInspector();

  info("Checking PlaybackRateSelector existence");
  const selectEl = panel.querySelector(".playback-rate-selector");
  ok(selectEl, "PlaybackRateSelector element exists");

  info("Checking PlaybackRateSelector options");
  const expectedPlaybackRates = [0.01, 0.1, 0.25, 0.5, 1, 2, 5, 10];
  await assertPlaybackRateMultiplierOptions(selectEl, expectedPlaybackRates);
  // When not set, the browsing context flag is 1
  await assertPlaybackRateMultiplier(1);

  info("Check setting a playback rate multiplier different than 1");
  await changePlaybackRateMultiplierSelector(animationInspector, panel, 0.5);
  await assertPlaybackRateMultiplier(0.5);

  info("Checking playback rate multiplier after setting it back to 1");
  await changePlaybackRateMultiplierSelector(animationInspector, panel, 1);
  await assertPlaybackRateMultiplier(1);
});

async function assertPlaybackRateMultiplier(rate) {
  await SpecialPowers.spawn(gBrowser.selectedBrowser, [rate], r => {
    is(
      content.browsingContext.animationsPlayBackRateMultiplier,
      r,
      "Expected browsingContext.animationsPlayBackRateMultiplier"
    );
  });
}

async function assertPlaybackRateMultiplierOptions(
  selectEl,
  expectedPlaybackRates
) {
  await waitUntil(() => {
    if (selectEl.options.length !== expectedPlaybackRates.length) {
      return false;
    }

    for (let i = 0; i < selectEl.options.length; i++) {
      const optionEl = selectEl.options[i];
      const expectedPlaybackRate = expectedPlaybackRates[i];
      if (Number(optionEl.value) !== expectedPlaybackRate) {
        return false;
      }
    }

    return true;
  });
  ok(true, "Content of playback rate options are correct");
}
