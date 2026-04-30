import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";
import { tourSteps, type TourStep } from "./tour-steps";

/** Wait for an element matching ``selector`` to exist in the DOM, up to
 * ``timeoutMs``. Returns the element or null on timeout. */
async function waitForElement(
  selector: string,
  timeoutMs = 1500,
): Promise<Element | null> {
  const start = performance.now();
  while (performance.now() - start < timeoutMs) {
    const el = document.querySelector(selector);
    if (el) return el;
    await new Promise((r) => setTimeout(r, 60));
  }
  return null;
}

let activeDriver: Driver | null = null;

/** Run the onboarding tour. Steps marked ``requiresAdmin`` are skipped for
 * non-admin staff. ``navigate`` changes routes between steps; we wait for
 * the destination's anchor to render before driver.js starts the step. */
export async function runTour(
  navigate: (path: string) => void,
  isAdmin: boolean,
): Promise<void> {
  if (activeDriver) return;

  const steps: TourStep[] = tourSteps.filter(
    (s) => !s.requiresAdmin || isAdmin,
  );
  if (steps.length === 0) return;

  let idx = 0;

  const driverInstance = driver({
    showProgress: true,
    allowClose: true,
    overlayOpacity: 0.55,
    progressText: "Step {{current}} of {{total}}",
    onCloseClick: () => {
      driverInstance.destroy();
    },
    onDestroyed: () => {
      activeDriver = null;
    },
  });
  activeDriver = driverInstance;

  async function advanceTo(stepIndex: number): Promise<void> {
    if (stepIndex >= steps.length) {
      driverInstance.destroy();
      return;
    }
    const step = steps[stepIndex];
    if (step.navigateTo) {
      navigate(step.navigateTo);
      // Give the destination time to render before highlighting.
      await new Promise((r) => setTimeout(r, 250));
    }
    let element: Element | null = null;
    if (step.element) {
      element = await waitForElement(step.element);
    }

    driverInstance.highlight({
      element: element ?? undefined,
      popover: {
        title: step.popover.title,
        description: step.popover.description,
        side: step.popover.side,
        align: step.popover.align,
        showButtons: ["next", "previous", "close"],
        nextBtnText:
          stepIndex === steps.length - 1 ? "Done" : "Next",
        prevBtnText: "Back",
        onNextClick: () => {
          idx = stepIndex + 1;
          advanceTo(idx).catch(() => driverInstance.destroy());
        },
        onPrevClick: () => {
          idx = Math.max(0, stepIndex - 1);
          advanceTo(idx).catch(() => driverInstance.destroy());
        },
      },
    });
  }

  await advanceTo(idx);
}
