import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import UserLogin from "./UserLogin";
import { UserAuthProvider } from "./UserAuthContext";

// The sign-in popover's controls used to be ~28px (mode toggle) and ~36px
// (submit), below the app-wide 44px touch-target convention (WCAG 2.5.5).
// jsdom has no layout engine, so the fix is pinned by class rather than by a
// measured height -- the same approach LayerToggle.test.tsx uses.
function renderLogin() {
  return render(
    <UserAuthProvider>
      <UserLogin />
    </UserAuthProvider>,
  );
}

describe("UserLogin touch targets", () => {
  it("gives the login/register mode toggle the 44px min height", () => {
    renderLogin();
    const group = screen.getByRole("group", { name: "Choose action" });

    for (const name of ["Sign in", "Register"]) {
      const toggle = within(group).getByRole("button", { name });
      expect(toggle).toHaveClass("min-h-11");
      expect(toggle).toHaveClass("inline-flex");
    }
  });

  it("gives the submit action the 44px min height", () => {
    renderLogin();
    // The mode toggle also has a "Sign in" button; the submit is the one
    // whose type is submit.
    const submit = screen
      .getAllByRole("button", { name: "Sign in" })
      .find((button) => button.getAttribute("type") === "submit");

    expect(submit).toBeDefined();
    expect(submit).toHaveClass("min-h-11");
    expect(submit).toHaveClass("inline-flex");
  });
});
