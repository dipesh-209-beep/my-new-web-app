import { afterEach, describe, expect, it, vi, Mock } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NavBar from "./NavBar";
import { usePathname } from "next/navigation";
import { UserAuthProvider } from "../user/UserAuthContext";
import { setUsername, setUserToken } from "@/lib/userApi";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(),
}));

// A stand-in for the real sign-in form that exposes one focusable action, so
// the tests can check the disclosure's keyboard reach and Escape behaviour
// without depending on the login flow.
vi.mock("../user/UserLogin", () => ({
  default: ({ onDone }: { onDone: () => void }) => (
    <div data-testid="user-login">
      <button type="button" onClick={onDone}>
        Continue
      </button>
    </div>
  ),
}));

function renderNav() {
  (usePathname as Mock).mockReturnValue("/");
  return render(
    <UserAuthProvider>
      <NavBar />
    </UserAuthProvider>,
  );
}

afterEach(() => {
  window.localStorage.clear();
});

describe("NavBar sign-in disclosure", () => {
  it("opens and closes the popup from the trigger and keeps aria-expanded in sync", async () => {
    const user = userEvent.setup();
    renderNav();
    const trigger = screen.getByRole("button", { name: "Sign in" });

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("user-login")).toBeNull();

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("user-login")).toBeInTheDocument();

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("user-login")).toBeNull();
  });

  it("is a disclosure, not a menu: no aria-haspopup, and aria-controls resolves to the popup", async () => {
    const user = userEvent.setup();
    renderNav();
    const trigger = screen.getByRole("button", { name: "Sign in" });

    // The popup is a login form / sign-out panel, not a menu or dialog, so
    // aria-haspopup would be a false promise.
    expect(trigger).not.toHaveAttribute("aria-haspopup");
    // Nothing to point at while collapsed.
    expect(trigger).not.toHaveAttribute("aria-controls");

    await user.click(trigger);
    const controls = trigger.getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    expect(document.getElementById(controls as string)).toBeInTheDocument();
    expect(trigger).not.toHaveAttribute("aria-haspopup");
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    const user = userEvent.setup();
    renderNav();
    const trigger = screen.getByRole("button", { name: "Sign in" });

    await user.click(trigger);
    const action = screen.getByRole("button", { name: "Continue" });
    action.focus();
    expect(action).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("user-login")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in" })).toHaveFocus();
  });

  it("lets a keyboard user tab from the trigger into the popup's action", async () => {
    const user = userEvent.setup();
    renderNav();
    const trigger = screen.getByRole("button", { name: "Sign in" });

    await user.click(trigger);
    expect(trigger).toHaveFocus();

    await user.tab();
    expect(screen.getByRole("button", { name: "Continue" })).toHaveFocus();
  });

  it("closes on an outside click but stays open for clicks inside the popup", async () => {
    const user = userEvent.setup();
    renderNav();
    const trigger = screen.getByRole("button", { name: "Sign in" });

    await user.click(trigger);
    await user.click(screen.getByTestId("user-login"));
    expect(screen.getByTestId("user-login")).toBeInTheDocument();

    await user.click(document.body);
    expect(screen.queryByTestId("user-login")).toBeNull();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("names the signed-in trigger as an account control", async () => {
    setUserToken("test-jwt");
    setUsername("dipesh");
    renderNav();

    const trigger = await screen.findByRole("button", { name: "Account: dipesh" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });
});

describe("NavBar brand link", () => {
  // D1: the visible "KTM"/"Transit" text used to be `hidden` below the sm
  // breakpoint, leaving only the aria-hidden "K" mark, so the link had no
  // accessible name on mobile. It is now `sr-only` + `sm:not-sr-only`, which
  // keeps the text in the accessibility tree at every width.
  //
  // jsdom does not apply Tailwind's responsive media queries, so this only
  // proves the DOM produces the right name and word boundary -- it cannot
  // prove the text survives below `sm` in a real browser.
  it("has a non-empty accessible name containing both brand words, separated", () => {
    renderNav();
    const brand = screen.getByRole("link", { name: "KTM Transit" });
    expect(brand).toHaveAttribute("href", "/");
    // Guard the word boundary: the two words must not be concatenated.
    expect(screen.queryByRole("link", { name: "KTMTransit" })).toBeNull();
  });
});

describe("NavBar sign-in popup narrow-screen sizing", () => {
  // D6: the panel is a fixed 20rem box anchored to the bar's right edge. On a
  // 320 px viewport it used to overhang the left of the screen -- 20rem is
  // wider than the bar's 320 px content column, and the trigger sits a whole
  // button in from the right, so the box's right edge is well short of the
  // viewport edge. The fix anchors the panel to the actions group (so
  // `right-0` resolves against the bar, not the trigger) and caps its width
  // to the viewport, while a wrapped actions row is kept flush right below
  // `sm`. jsdom has no layout engine, so this pins the classes the fix rests
  // on -- it cannot prove the panel fits at 320 px.
  it("caps the popup width to the viewport while keeping the 20rem desktop width", async () => {
    const user = userEvent.setup();
    renderNav();
    const trigger = screen.getByRole("button", { name: "Sign in" });
    await user.click(trigger);

    const panel = document.getElementById(trigger.getAttribute("aria-controls") as string);
    expect(panel).not.toBeNull();
    expect(panel).toHaveClass("w-80");
    expect(panel).toHaveClass("max-w-[calc(100vw-1rem)]");
  });

  it("anchors the popup to the right-hand actions group, not the trigger's wrapper", async () => {
    const user = userEvent.setup();
    renderNav();
    const trigger = screen.getByRole("button", { name: "Sign in" });
    await user.click(trigger);

    const group = screen.getByRole("button", { name: "Minimize navigation bar" }).parentElement;
    const panel = document.getElementById(trigger.getAttribute("aria-controls") as string);
    expect(group).toHaveClass("relative");
    expect(group).toContainElement(panel);
  });

  it("keeps the actions group flush right only below sm, so desktop spacing is unchanged", () => {
    renderNav();
    const group = screen.getByRole("button", { name: "Minimize navigation bar" }).parentElement;
    // `max-sm:ml-auto` right-aligns the group when the header wraps, but must
    // not leak into `sm` and up, where `justify-between` already does it.
    expect(group).toHaveClass("max-sm:ml-auto");
    expect(group).not.toHaveClass("ml-auto");
  });
});

describe("NavBar sign-out touch target", () => {
  // The sign-out action sits inside the account popup and used to be only
  // ~32px tall (px-3 py-1.5). It now carries the app-wide 44px convention
  // (WCAG 2.5.5), same as the other authored controls -- asserted by class
  // because jsdom has no layout engine.
  it("gives the sign-out action the 44px min height", async () => {
    setUserToken("test-jwt");
    setUsername("dipesh");
    const user = userEvent.setup();
    renderNav();

    const trigger = await screen.findByRole("button", { name: "Account: dipesh" });
    await user.click(trigger);

    const signOut = screen.getByRole("button", { name: "Sign out" });
    expect(signOut).toHaveClass("min-h-11");
    expect(signOut).toHaveClass("inline-flex");
  });
});
