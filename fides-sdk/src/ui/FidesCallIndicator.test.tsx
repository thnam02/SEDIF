import { act, render } from "@testing-library/react-native"
import { Text } from "react-native"

import { FidesSession } from "../FidesSession"
import { FidesCallIndicator, FidesCallNotice } from "./FidesCallIndicator"
import { showCallNotice } from "./callNotice"
import { copyVi } from "./copy.vi"

function Home() {
  return <Text>Trang chủ</Text>
}

beforeEach(() => {
  jest.useFakeTimers()
  FidesSession.start()
  FidesSession.setSimulatedCall(false)
})

afterEach(() => {
  FidesSession.setSimulatedCall(false)
  jest.useRealTimers()
})

describe("FidesCallIndicator", () => {
  it("hiện ở màn tiền và ẩn ở trang chủ", () => {
    FidesSession.setSimulatedCall(true)
    const home = render(<Home />)
    expect(home.queryByText(copyVi.callPill)).toBeNull()
    const amount = render(<FidesCallIndicator />)
    expect(amount.getByTestId("call-pill")).toBeTruthy()
    expect(amount.getByText(copyVi.callPill)).toBeTruthy()
  })

  it("cuộc gọi vừa kết thúc rồi ẩn sau 10 giây", () => {
    FidesSession.setSimulatedCall(true)
    const view = render(<FidesCallIndicator />)
    act(() => {
      FidesSession.setSimulatedCall(false)
    })
    expect(view.getByText(copyVi.callEnded)).toBeTruthy()
    act(() => {
      jest.advanceTimersByTime(10_000)
    })
    expect(view.queryByText(copyVi.callEnded)).toBeNull()
  })
})

describe("FidesCallNotice", () => {
  it("tôn trọng giới hạn 3 lần, demo thì không", () => {
    expect(showCallNotice(true, 2, 3, false)).toBe(true)
    expect(showCallNotice(true, 3, 3, false)).toBe(false)
    expect(showCallNotice(false, 0, 3, false)).toBe(false)
    expect(showCallNotice(true, 9, 3, true)).toBe(true)
    const blocked = render(<FidesCallNotice inCall used={3} max={3} demo={false} />)
    expect(blocked.queryByText(copyVi.callNotice)).toBeNull()
    const demo = render(<FidesCallNotice inCall used={9} max={3} demo />)
    expect(demo.getByText(copyVi.callNotice)).toBeTruthy()
  })
})
