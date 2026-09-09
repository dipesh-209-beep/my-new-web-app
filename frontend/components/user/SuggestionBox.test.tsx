import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserAuthProvider } from "@/components/user/UserAuthContext";
import SuggestionBox from "@/components/user/SuggestionBox";
import { setUsername, setUserToken } from "@/lib/userApi";
import { Suggestion } from "@/types/route";

// The box's data flow (list + submit/vote) is delegated to lib/userApi --
// mock those two network calls, keep the localStorage helpers real.
vi.mock("@/lib/userApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/userApi")>();
  return {
    ...actual,
    userListSuggestions: vi.fn(),
    userSubmitSuggestion: vi.fn(),
  };
});

const { userListSuggestions, userSubmitSuggestion } = await import("@/lib/userApi");

function makeSuggestion(overrides: Partial<Suggestion> = {}): Suggestion {
  return {
    suggestion_id: 1,
    target_type: "stop",
    target_id: "S0001",
    suggestion_type: "stop_name_change",
    payload: { stop_name: "Better Name" },
    status: "pending",
    vote_count: 2,
    created_at: "2026-09-09T11:59:36.319409+00:00",
    submitted_by: "alice",
    reviewed_by: null,
    voted_by_me: false,
    ...overrides,
  };
}

function renderBox(props: {
  targetType?: "stop" | "route";
  targetId?: string;
  currentStops?: { sequence_no: number; stop: { stop_id: string; stop_name: string } }[];
} = {}) {
  return render(
    <UserAuthProvider>
      <SuggestionBox
        targetType={props.targetType ?? "stop"}
        targetId={props.targetId ?? "S0001"}
        currentStops={props.currentStops as never}
      />
    </UserAuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  // Both paths hit userListSuggestions on mount; empty list keeps the
  // assertions focused on auth/form behavior. Tests that care about the
  // list override the return value.
  vi.mocked(userListSuggestions).mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("SuggestionBox — signed out", () => {
  it("prompts to sign in before showing any suggestion form", async () => {
    renderBox();

    expect(await screen.findByText(/sign in to suggest a change or back one/i)).toBeInTheDocument();
    // Both the login/register toggle and the submit button say "Sign in".
    expect(screen.getAllByRole("button", { name: /sign in/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /suggest name/i })).not.toBeInTheDocument();
  });
});

describe("SuggestionBox — signed in", () => {
  beforeEach(() => {
    setUserToken("user-jwt");
    setUsername("alice");
  });

  it("shows the name-suggestion form once the session hydrates", async () => {
    renderBox({ targetType: "stop" });

    expect(await screen.findByText(/no pending suggestions for this stop yet/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/new stop name/i)).toBeInTheDocument();
    // Suggesting with an empty input is disabled.
    expect(screen.getByRole("button", { name: /suggest name/i })).toBeDisabled();
  });

  it("submits a stop name change with the signed-in token", async () => {
    vi.mocked(userSubmitSuggestion).mockResolvedValueOnce(makeSuggestion({ vote_count: 1 }));
    renderBox({ targetType: "stop" });

    const input = await screen.findByPlaceholderText(/new stop name/i);
    await userEvent.type(input, "Baneshwor Chowk");
    await userEvent.click(screen.getByRole("button", { name: /suggest name/i }));

    expect(userSubmitSuggestion).toHaveBeenCalledWith(
      {
        target_type: "stop",
        target_id: "S0001",
        suggestion_type: "stop_name_change",
        payload: { stop_name: "Baneshwor Chowk" },
      },
      "user-jwt",
    );
    expect(await screen.findByText(/suggestion submitted/i)).toBeInTheDocument();
  });

  it("lists pending suggestions with working back-it votes", async () => {
    vi.mocked(userListSuggestions).mockResolvedValue([makeSuggestion()]);
    renderBox({ targetType: "stop" });

    const backButton = await screen.findByRole("button", { name: /back this/i });
    expect(screen.getAllByText(/better name/i).length).toBeGreaterThan(0);

    vi.mocked(userSubmitSuggestion).mockResolvedValueOnce(
      makeSuggestion({ vote_count: 3 }),
    );
    // After the vote lands, the refreshed list reports voted_by_me.
    vi.mocked(userListSuggestions).mockResolvedValueOnce([
      makeSuggestion({ vote_count: 3, voted_by_me: true }),
    ]);
    await userEvent.click(backButton);

    expect(userSubmitSuggestion).toHaveBeenCalledWith(
      {
        target_type: "stop",
        target_id: "S0001",
        suggestion_type: "stop_name_change",
        payload: { stop_name: "Better Name" },
      },
      "user-jwt",
    );
    // The refreshed list (server now reports voted_by_me) flips to "Backed".
    expect(await screen.findByRole("button", { name: /backed/i })).toBeDisabled();
  });

  it("hides the backing button for suggestions the user already voted for", async () => {
    vi.mocked(userListSuggestions).mockResolvedValue([makeSuggestion({ voted_by_me: true })]);
    renderBox({ targetType: "stop" });

    expect(await screen.findByText(/backed/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /back this/i })).not.toBeInTheDocument();
  });
});

describe("SuggestionBox — route reorder", () => {
  beforeEach(() => {
    setUserToken("user-jwt");
    setUsername("alice");
  });

  it("offers a reorder control and submits the sequence payload with current sequence numbers", async () => {
    const currentStops = [
      { sequence_no: 1, stop: { stop_id: "S0001", stop_name: "Ratna Park" } },
      { sequence_no: 2, stop: { stop_id: "S0002", stop_name: "Koteshwor" } },
      { sequence_no: 3, stop: { stop_id: "S0003", stop_name: "Tinkune" } },
    ];
    vi.mocked(userSubmitSuggestion).mockResolvedValueOnce(
      makeSuggestion({ suggestion_type: "stop_sequence_change", vote_count: 1 }),
    );
    renderBox({ targetType: "route", currentStops });

    // Move "Koteshwor" to the front.
    await userEvent.click(await screen.findByRole("button", { name: /move koteshwor up/i }));

    await userEvent.click(screen.getByRole("button", { name: /suggest this order/i }));

    expect(userSubmitSuggestion).toHaveBeenCalledWith(
      {
        target_type: "route",
        target_id: "S0001",
        suggestion_type: "stop_sequence_change",
        payload: { sequence: [2, 1, 3] },
      },
      "user-jwt",
    );
  });

  it("disables the reorder submit until the order actually changes", async () => {
    const currentStops = [
      { sequence_no: 1, stop: { stop_id: "S0001", stop_name: "Ratna Park" } },
      { sequence_no: 2, stop: { stop_id: "S0002", stop_name: "Koteshwor" } },
    ];
    renderBox({ targetType: "route", currentStops });

    const submitButton = await screen.findByRole("button", { name: /suggest this order/i });
    expect(submitButton).toBeDisabled();
  });
});