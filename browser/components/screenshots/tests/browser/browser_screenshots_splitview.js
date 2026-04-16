/* Any copyright is dedicated to the Public Domain.
   http://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

add_setup(async function () {
  await SpecialPowers.pushPrefEnv({
    set: [["test.wait300msAfterTabSwitch", true]],
  });
});

async function cancelPanel(helper) {
  let exitObserved = TestUtils.topicObserved("screenshots-exit");
  EventUtils.synthesizeKey("KEY_Escape");
  await helper.waitForPanelClosed();
  await exitObserved;
}

function assertPanelWithinRect(panel, refRect) {
  // the positioned box has 0 width; its the buttons we want to measure
  const rect = panel.firstElementChild.getBoundingClientRect();
  Assert.greater(rect.width, 1, "Panel has width");
  Assert.greaterOrEqual(
    rect.left,
    refRect.left,
    "Left edge is >= reference edge"
  );
  Assert.lessOrEqual(
    rect.right,
    refRect.right,
    "Right edge is <= the reference edge"
  );
}

add_task(async function test_buttonsPanelWithSplitView() {
  const helper = new ScreenshotsHelper(gBrowser.selectedBrowser);
  let buttonsPanel;

  const tab1 = await addTabAndLoadBrowser();
  const tab2 = await addTabAndLoadBrowser();
  const tab3 = await addTabAndLoadBrowser();
  await BrowserTestUtils.switchTab(gBrowser, tab1);

  const tabToTabPanels = new WeakMap();
  for (let tab of [tab1, tab2, tab3]) {
    tabToTabPanels.set(tab, document.getElementById(tab.linkedPanel));
  }

  gBrowser.addTabSplitView([tab1, tab2]);
  helper.triggerUIFromToolbar();
  buttonsPanel = await helper.waitForPanel();

  // make sure the butons panel is visually associated with the selected tab
  assertPanelWithinRect(
    buttonsPanel,
    tabToTabPanels.get(tab1).getBoundingClientRect()
  );
  await cancelPanel(helper);

  // switch to the other side of the splitview
  await BrowserTestUtils.switchTab(gBrowser, tab2);
  helper.triggerUIFromToolbar();
  buttonsPanel = await helper.waitForPanel();

  // make sure the butons panel is visually associated with the selected tab
  assertPanelWithinRect(
    buttonsPanel,
    tabToTabPanels.get(tab2).getBoundingClientRect()
  );

  // With the panel still open, close the selected tab
  // and verify we sill get the panel next time.
  const tab2closed = BrowserTestUtils.waitForTabClosing(tab2);
  BrowserTestUtils.removeTab(tab2);
  await tab2closed;

  await BrowserTestUtils.switchTab(gBrowser, tab3);
  helper.triggerUIFromToolbar();
  buttonsPanel = await helper.waitForPanel();

  // make sure the butons panel is visually associated with the selected tab
  assertPanelWithinRect(
    buttonsPanel,
    tabToTabPanels.get(tab3).getBoundingClientRect()
  );
  await cancelPanel(helper);

  BrowserTestUtils.removeTab(tab1);
  BrowserTestUtils.removeTab(tab3);
});

add_task(async function test_cancelSelectionClosesInitialSibling() {
  const tab1 = await addTabAndLoadBrowser();
  const tab2 = await addTabAndLoadBrowser();
  await BrowserTestUtils.switchTab(gBrowser, tab1);

  gBrowser.addTabSplitView([tab1, tab2]);

  const helper1 = new ScreenshotsHelper(tab1.linkedBrowser);
  const helper2 = new ScreenshotsHelper(tab2.linkedBrowser);

  // In the left browser, open screenshots and draw a selection.
  helper1.triggerUIFromToolbar();
  await helper1.waitForOverlay();
  await helper1.dragOverlay(10, 10, 300, 300);
  await helper1.assertStateChange("selected");

  // Switch to the right browser and open screenshots there (INITIAL state).
  await BrowserTestUtils.switchTab(gBrowser, tab2);
  helper2.triggerUIFromToolbar();
  await BrowserTestUtils.waitForCondition(
    async () => await helper2.isOverlayInitialized(),
    "Right browser overlay should be initialized"
  );

  // Verify that tab1 is still in OVERLAYSELECTION - the selection made in the
  // left browser should be preserved while the right browser is active.
  await helper1.assertStateChange("selected");
  ok(
    await helper1.isOverlayInitialized(),
    "Left browser overlay should still be visible "
  );

  // Without switching tabs, click the [X] button in the left browser to clear
  // the selection. This returns the left browser to the initial state. Because
  // the right browser is also in the initial state, it should be cancelled.
  let exitObserved = TestUtils.topicObserved("screenshots-exit");
  await helper1.clickCancelButton();

  await helper1.assertStateChange("crosshairs");
  await exitObserved;
  await BrowserTestUtils.waitForCondition(
    async () => !(await helper2.isOverlayInitialized()),
    "Right browser overlay should be closed after left browser cancelled its selection"
  );

  await BrowserTestUtils.switchTab(gBrowser, tab1);
  await cancelPanel(helper1);

  BrowserTestUtils.removeTab(tab1);
  BrowserTestUtils.removeTab(tab2);
});
