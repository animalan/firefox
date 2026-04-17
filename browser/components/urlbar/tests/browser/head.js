/* Any copyright is dedicated to the Public Domain.
   http://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

ChromeUtils.defineESModuleGetters(this, {
  AboutNewTab: "resource:///modules/AboutNewTab.sys.mjs",
  BrowsetUIUtils: "resource:///modules/BrowserUIUtils.sys.mjs",
  ExperimentAPI: "resource://nimbus/ExperimentAPI.sys.mjs",
  NimbusTestUtils: "resource://testing-common/NimbusTestUtils.sys.mjs",
  ObjectUtils: "resource://gre/modules/ObjectUtils.sys.mjs",
  PromptTestUtils: "resource://testing-common/PromptTestUtils.sys.mjs",
  ResetProfile: "resource://gre/modules/ResetProfile.sys.mjs",
  SearchUITestUtils: "resource://testing-common/SearchUITestUtils.sys.mjs",
  SearchUtils: "moz-src:///toolkit/components/search/SearchUtils.sys.mjs",
  TelemetryTestUtils: "resource://testing-common/TelemetryTestUtils.sys.mjs",
  UrlbarController:
    "moz-src:///browser/components/urlbar/UrlbarController.sys.mjs",
  UrlbarEventBufferer:
    "moz-src:///browser/components/urlbar/UrlbarEventBufferer.sys.mjs",
  UrlbarQueryContext:
    "moz-src:///browser/components/urlbar/UrlbarUtils.sys.mjs",
  UrlbarPrefs: "moz-src:///browser/components/urlbar/UrlbarPrefs.sys.mjs",
  UrlbarResult: "moz-src:///browser/components/urlbar/UrlbarResult.sys.mjs",
  UrlbarSearchUtils:
    "moz-src:///browser/components/urlbar/UrlbarSearchUtils.sys.mjs",
  UrlbarUtils: "moz-src:///browser/components/urlbar/UrlbarUtils.sys.mjs",
  UrlbarView: "moz-src:///browser/components/urlbar/UrlbarView.sys.mjs",
  sinon: "resource://testing-common/Sinon.sys.mjs",
});

ChromeUtils.defineLazyGetter(this, "PlacesFrecencyRecalculator", () => {
  return Cc["@mozilla.org/places/frecency-recalculator;1"].getService(
    Ci.nsIObserver
  ).wrappedJSObject;
});

SearchUITestUtils.init(this);

let sandbox;

Services.scriptloader.loadSubScript(
  "chrome://mochitests/content/browser/browser/components/urlbar/tests/browser/head-common.js",
  this
);

registerCleanupFunction(async () => {
  // Ensure the Urlbar popup is always closed at the end of a test, to save having
  // to do it within each test.
  await UrlbarTestUtils.promisePopupClose(window);
});

/**
 * Puts all CustomizableUI widgetry back to their default locations, and
 * then fires the `aftercustomization` toolbox event so that UrlbarInput
 * knows to reinitialize itself.
 *
 * @param {window} [win=window]
 *   The top-level browser window to fire the `aftercustomization` event in.
 */
function resetCUIAndReinitUrlbarInput(win = window) {
  CustomizableUI.reset();
  CustomizableUI.dispatchToolboxEvent("aftercustomization", {}, win);
}
/**
 * Asserts a search term is in the url bar and state values are
 * what they should be.
 *
 * @param {string} searchString
 *   String that should be matched in the url bar.
 * @param {object | null} options
 *   Options for the assertions.
 * @param {Window | null} options.window
 *   Window to use for tests.
 * @param {string | null} options.pageProxyState
 *   The pageproxystate that should be expected.
 * @param {string | null} options.userTypedValue
 *   The userTypedValue that should be expected.
 * @param {boolean | null} options.persistSearchTerms
 *   The attribute persistsearchterms that should be expected.
 */
function assertSearchStringIsInUrlbar(
  searchString,
  {
    win = window,
    pageProxyState = "invalid",
    userTypedValue = searchString,
    persistSearchTerms = true,
  } = {}
) {
  Assert.equal(
    win.gURLBar.value,
    searchString,
    `Search string should be the urlbar value.`
  );
  let state = win.gURLBar.getBrowserState(win.gBrowser.selectedBrowser);
  Assert.equal(
    state.persist?.searchTerms,
    searchString,
    `Search terms should match.`
  );
  Assert.equal(
    win.gBrowser.userTypedValue,
    userTypedValue,
    "userTypedValue should match."
  );
  Assert.equal(
    win.gURLBar.getAttribute("pageproxystate"),
    pageProxyState,
    "Pageproxystate should match."
  );
  if (persistSearchTerms) {
    Assert.ok(
      win.gURLBar.hasAttribute("persistsearchterms"),
      "Urlbar has persistsearchterms attribute."
    );
  } else {
    Assert.ok(
      !win.gURLBar.hasAttribute("persistsearchterms"),
      "Urlbar does not have persistsearchterms attribute."
    );
  }
}

async function searchWithTab(
  searchString,
  tab = null,
  engine = SearchService.defaultEngine,
  expectedPersistedSearchTerms = true
) {
  if (!tab) {
    tab = await BrowserTestUtils.openNewForegroundTab(gBrowser);
  }

  let [expectedSearchUrl] = UrlbarUtils.getSearchQueryUrl(engine, searchString);
  let browserLoadedPromise = BrowserTestUtils.browserLoaded(
    tab.linkedBrowser,
    false,
    expectedSearchUrl
  );

  gURLBar.focus();
  await UrlbarTestUtils.promiseAutocompleteResultPopup({
    window,
    waitForFocus,
    value: searchString,
    fireInputEvent: true,
    selectionStart: 0,
    selectionEnd: searchString.length - 1,
  });
  EventUtils.synthesizeKey("KEY_Enter");
  await browserLoadedPromise;

  if (expectedPersistedSearchTerms) {
    info("Load a tab with search terms persisting in the urlbar.");
    assertSearchStringIsInUrlbar(searchString);
  }

  return { tab, expectedSearchUrl };
}

async function focusSwitcher(win = window) {
  await UrlbarTestUtils.promiseAutocompleteResultPopup({
    window: win,
    waitForFocus: true,
    value: "",
    fireInputEvent: true,
  });
  Assert.ok(win.gURLBar.hasAttribute("focused"), "Urlbar was focused");

  EventUtils.synthesizeKey("KEY_Tab", { shiftKey: true }, win);
  let switcher = win.gURLBar.querySelector(".searchmode-switcher");
  await BrowserTestUtils.waitForCondition(
    () => win.document.activeElement == switcher
  );
  Assert.ok(true, "Search mode switcher was focused");
}

/**
 * Clears the SAP telemetry probes (SEARCH_COUNTS and all of Glean).
 */
function clearSAPTelemetry() {
  TelemetryTestUtils.getAndClearKeyedHistogram("SEARCH_COUNTS");
  Services.fog.testResetFOG();
}
