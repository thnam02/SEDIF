import { deriveCallBlock, inferObservability, inferTransport } from "./callBlock"

const idle = {
  activeAtStart: false as boolean | null,
  startedDuring: false,
  endedDuring: false,
  activeNow: false as boolean | null,
}

describe("deriveCallBlock", () => {
  it("không báo none khi không quan sát được", () => {
    expect(deriveCallBlock({ ...idle, observability: "unavailable", transport: "unknown" }).state).toBe("unknown")
    expect(deriveCallBlock({ ...idle, observability: "supported_not_observed", transport: "unknown" }).state).toBe("unknown")
    expect(deriveCallBlock({ ...idle, activeNow: null, observability: "unavailable", transport: "unknown" }).state).toBe("unknown")
  })

  it("đã quan sát và không có cuộc thì none", () => {
    expect(deriveCallBlock({ ...idle, observability: "supported_observed", transport: "unknown" }).state).toBe("none")
  })

  it("cuộc còn mở là active, cuộc đã kết thúc trong phiên là ended_in_session", () => {
    expect(
      deriveCallBlock({
        observability: "supported_observed",
        transport: "cellular",
        activeAtStart: true,
        startedDuring: false,
        endedDuring: false,
        activeNow: true,
      }).state,
    ).toBe("active")
    expect(
      deriveCallBlock({
        observability: "supported_observed",
        transport: "unknown",
        activeAtStart: true,
        startedDuring: false,
        endedDuring: true,
        activeNow: false,
      }).state,
    ).toBe("ended_in_session")
    expect(
      deriveCallBlock({
        observability: "supported_observed",
        transport: "voip",
        activeAtStart: false,
        startedDuring: true,
        endedDuring: false,
        activeNow: true,
      }),
    ).toEqual({ state: "active", observability: "supported_observed", transport: "voip" })
  })

  it("iOS không phân biệt kênh, Android tách cellular và voip", () => {
    expect(inferTransport("ios", { in_call: true, voip_suspected: true })).toBe("unknown")
    expect(inferTransport("android", { in_call: true, voip_suspected: false })).toBe("cellular")
    expect(inferTransport("android", { in_call: null, voip_suspected: true })).toBe("voip")
    expect(inferTransport("android", { in_call: false, voip_suspected: true })).toBe("voip")
    expect(inferObservability("android", { in_call: null })).toBe("unavailable")
    expect(inferObservability("android", { in_call: false })).toBe("supported_observed")
    expect(inferObservability("ios", { in_call: null })).toBe("supported_not_observed")
  })
})
