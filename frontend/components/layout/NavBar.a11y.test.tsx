import { describe, expect, it, vi, Mock } from "vitest";
import { render } from "@testing-library/react";
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

describe("NavBar a11y", () => {
  it("has no axe violations", async () => {
    (usePathname as Mock).mockReturnValue("/");
    const { container } = render(
      <UserAuthProvider>
        <NavBar />
      </UserAuthProvider>,
    );
    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });
});
