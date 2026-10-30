import { fireEvent, render } from "@testing-library/react-native"

import { FidesGuardianRequest, FidesIntervention, FidesPermissionScreen } from "./FidesIntervention"
import { copyVi } from "./copy.vi"
import { formatHold, type FidesDecision } from "./screen"

function decision(partial: Partial<FidesDecision> & Pick<FidesDecision, "level" | "actions">): FidesDecision {
  return {
    decision_id: "dec-1",
    reasons: [],
    hold_until: null,
    countdown_until: null,
    payee_name: "Trần Bình Minh",
    amount_text: "500.000 đ",
    channel: "Chuyển khoản",
    ...partial,
  }
}

describe("FidesIntervention", () => {
  it("info", () => {
    const onRespond = jest.fn()
    const view = render(<FidesIntervention decision={decision({ level: "info", actions: ["info"] })} onRespond={onRespond} />)
    expect(view.getByTestId("fides-info")).toBeTruthy()
    expect(view.getByText(copyVi.info.body)).toBeTruthy()
    fireEvent.press(view.getByText(copyVi.info.no))
    expect(onRespond).toHaveBeenCalledWith({ decision_id: "dec-1", cancel: true })
  })

  it("banner", () => {
    const view = render(<FidesIntervention decision={decision({ level: "banner", actions: ["g1_banner"] })} onRespond={jest.fn()} />)
    expect(view.getByTestId("fides-banner")).toBeTruthy()
    expect(view.getByText(copyVi.banner.body)).toBeTruthy()
    expect(view.queryByText(copyVi.simo)).toBeNull()
  })

  it("simo_listed", () => {
    const onRespond = jest.fn()
    const view = render(
      <FidesIntervention
        decision={decision({ level: "banner", actions: ["g1_banner"], reasons: [{ code: "simo_listed", text: copyVi.simo }] })}
        onRespond={onRespond}
      />,
    )
    expect(view.getByTestId("fides-simo_listed")).toBeTruthy()
    expect(view.getByText(copyVi.simo)).toBeTruthy()
    fireEvent.press(view.getByText(copyVi.cancel))
    expect(onRespond).toHaveBeenCalledWith({ decision_id: "dec-1", cancel: true })
  })

  it("màn medium và high cũ không còn", () => {
    const medium = render(<FidesIntervention decision={decision({ level: "medium", actions: ["pause_and_verify", "guardian_confirm"] })} onRespond={jest.fn()} />)
    expect(medium.queryByTestId("fides-pause_and_verify")).toBeNull()
    expect(medium.queryByText("Giao dịch đang được giữ an toàn trong 30 phút")).toBeNull()
    const high = render(<FidesIntervention decision={decision({ level: "high", actions: ["hangup_required", "recovery_window"] })} onRespond={jest.fn()} />)
    expect(high.queryByTestId("fides-hangup_required")).toBeNull()
    expect(high.queryByTestId("fides-countdown")).toBeNull()
  })

  it("show_chain", () => {
    const view = render(<FidesIntervention decision={decision({ level: "medium", actions: ["show_chain"] })} onRespond={jest.fn()} />)
    expect(view.getByTestId("fides-show_chain")).toBeTruthy()
    expect(view.getByText(copyVi.chain.steps[2])).toBeTruthy()
  })

  it("guardian_confirm_requested", () => {
    const view = render(
      <FidesIntervention decision={decision({ level: "medium", actions: ["pause_and_verify"], gate: "guardian_confirm_requested" })} onRespond={jest.fn()} />,
    )
    expect(view.getByTestId("fides-guardian_confirm_requested")).toBeTruthy()
    expect(view.getByText(copyVi.guardian.pending)).toBeTruthy()
  })

  it("short_wait lấy mốc từ server", () => {
    jest.useFakeTimers()
    jest.setSystemTime(new Date("2026-09-21T10:20:00+07:00"))
    const view = render(
      <FidesIntervention
        decision={decision({ level: "banner", actions: ["g1_banner"], gate: "short_wait", countdown_until: "2026-09-21T10:20:30+07:00" })}
        onRespond={jest.fn()}
      />,
    )
    expect(view.getByTestId("fides-short_wait")).toBeTruthy()
    expect(view.getByText("30s")).toBeTruthy()
    expect(view.queryByText(copyVi.countdown.body)).toBeNull()
    jest.useRealTimers()
  })

  it("held", () => {
    const view = render(
      <FidesIntervention
        decision={decision({ level: "none", actions: ["hold"], funds_status: "held", hold_until: "2026-09-21T13:52:00+07:00" })}
        onRespond={jest.fn()}
      />,
    )
    expect(view.getByTestId("fides-held")).toBeTruthy()
    expect(view.getByText(`${copyVi.held.expected}: ${formatHold("2026-09-21T13:52:00+07:00")}`)).toBeTruthy()
  })

  it("cancelled", () => {
    const view = render(
      <FidesIntervention decision={decision({ level: "high", actions: ["hangup_required"], funds_status: "cancelled" })} onRespond={jest.fn()} />,
    )
    expect(view.getByTestId("fides-cancelled")).toBeTruthy()
    expect(view.getByText(copyVi.cancelled.title)).toBeTruthy()
  })

  it("chữ lớn nhân 1,4", () => {
    const view = render(<FidesIntervention decision={decision({ level: "info", actions: ["info"] })} onRespond={jest.fn()} largeText />)
    expect(view.getByText(copyVi.info.title).props.style.fontSize).toBe(28)
    expect(view.getByText(copyVi.info.yes)).toBeTruthy()
    expect(view.getByText(copyVi.info.no)).toBeTruthy()
  })
})

describe("màn riêng", () => {
  it("quyền cuộc gọi", () => {
    const onAllow = jest.fn()
    const view = render(<FidesPermissionScreen onAllow={onAllow} onLater={jest.fn()} />)
    expect(view.getByText(copyVi.permission.body)).toBeTruthy()
    fireEvent.press(view.getByText(copyVi.allow))
    expect(onAllow).toHaveBeenCalled()
  })

  it("người giám hộ quyết định", () => {
    const onDecide = jest.fn()
    const view = render(<FidesGuardianRequest request={{ decision_id: "dec-9" }} onDecide={onDecide} />)
    fireEvent.press(view.getByText(copyVi.guardian.reject))
    expect(onDecide).toHaveBeenCalledWith({ decision_id: "dec-9", guardian_confirm: true })
    fireEvent.press(view.getByText(copyVi.guardian.approve))
    expect(onDecide).toHaveBeenCalledWith({ decision_id: "dec-9", guardian_confirm: false, cancel: true })
  })
})
