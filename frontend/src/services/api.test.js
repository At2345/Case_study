import { describe, it, expect, beforeEach, vi } from "vitest";
import { api, ApiError, detailToMessage, getToken, onUnauthorized } from "./api";

const jsonResponse = (status, body) => ({ ok: status < 400, status, json: async () => body });

describe("api client", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("stores the token on login and sends it as a bearer header", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(200, { access_token: "tok", username: "danny", role: "planner" }))
      .mockResolvedValueOnce(jsonResponse(200, { total: 0, items: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await api.login("danny", "pw");
    expect(getToken()).toBe("tok");
    await api.exceptions({ channel: "amazon", pending: "" });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toContain("/api/v1/exceptions?channel=amazon");
    expect(url).not.toContain("pending");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("turns validation arrays and strings into readable messages", () => {
    expect(detailToMessage("Reason code is required.")).toBe("Reason code is required.");
    expect(detailToMessage([{ msg: "bad a" }, { msg: "bad b" }])).toBe("bad a; bad b");
    expect(detailToMessage(undefined)).toBe("Request failed");
  });

  it("throws ApiError with the server detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, { detail: "Reason code is required." })));
    await expect(api.override({})).rejects.toMatchObject({ status: 422, message: "Reason code is required." });
  });

  it("clears the session and notifies on 401", async () => {
    sessionStorage.setItem("sop_token", "old");
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Invalid or expired token" })));
    await expect(api.summary()).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
    expect(handler).toHaveBeenCalled();
  });

  it("reports an unreachable backend clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.summary()).rejects.toMatchObject({ status: 0 });
  });
});
