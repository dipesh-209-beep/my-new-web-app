import { describe, expect, it, vi, Mock } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "vitest-axe";
import NavBar from "./NavBar";
import { usePathname } from "next/navigation";
import { UserAuthProvider } from "../user/UserAuthContext";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(),
}));

vi.mock("../user/UserLogin", () => ({
  default: () => <div data-testid="user-login">Sign in</div>,
}));

function renderNav() {
  (usePathname as Mock).mockReturnValue("/");
  return render(
    <UserAuthProvider>
      <NavBar />
    </UserAuthProvider>,
  );
}

describe("NavBar a11y", () => {
  it("has no axe violations", async () => {
    const { container } = renderNav();
    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });

  it("has no axe violations with the sign-in popup open", async () => {
    const user = userEvent.setup();
    const { container } = renderNav();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByTestId("user-login")).toBeInTheDocument();

    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });
});
