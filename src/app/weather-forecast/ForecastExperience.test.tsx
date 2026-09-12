import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ForecastExperience from "./ForecastExperience";
import { fixtureQuery, makeForecastFixture } from "@/lib/weather-forecast/test-fixture";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("fixture forecast experience", () => {
  it("shows a future timeline, source metadata and keyboard-operable hour controls", async () => {
    const fixture = makeForecastFixture();
    fixture.daily[0].wind_direction = null;
    fixture.daily[0].missingness.wind_direction = "calm-vector-direction-undefined";
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(fixture)));
    render(<ForecastExperience />);
    fireEvent.click(screen.getByRole("button", { name: "Load forecast" }));
    await screen.findByRole("slider", { name: "Forecast valid hour" });
    expect(screen.getByText(/Synthetic fixture — not a live forecast/)).toBeTruthy();
    expect(screen.getByText(/lead 24 hours/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next hour" }));
    expect(screen.getByText(/lead 25 hours/)).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getAllByText(/Complete day/)).toHaveLength(2);
    expect(screen.getByText(/Daily missingness: wind_direction: calm-vector-direction-undefined/)).toBeTruthy();
  });

  it("clears old results on a changed place and ignores delayed superseded reads", async () => {
    let finishOld: (response: Response) => void = () => {};
    vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>((resolve) => { finishOld = resolve; }));
    render(<ForecastExperience />);
    fireEvent.click(screen.getByRole("button", { name: "Load forecast" }));
    const oldSignal = vi.mocked(fetch).mock.calls[0][1]?.signal;
    fireEvent.change(screen.getByLabelText("Selected latitude"), { target: { value: "40.1" } });
    expect(oldSignal?.aborted).toBe(true);
    const selectedQuery = { ...fixtureQuery, lat: 40.1 };
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(makeForecastFixture(selectedQuery, 21))));
    fireEvent.click(screen.getByRole("button", { name: "Load forecast" }));
    await screen.findByText("21.0 degC");
    await act(async () => { finishOld(new Response(JSON.stringify(makeForecastFixture(fixtureQuery, 12)))); });
    expect(screen.queryByText("12.0 degC")).toBeNull();
    fireEvent.change(screen.getByLabelText("Selected longitude"), { target: { value: "-104" } });
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("21.0 degC")).toBeNull();
  });
});
