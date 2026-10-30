import { FidesSession } from "../FidesSession"

it("cuộc giả lập đang gọi là active, cúp giữa phiên vẫn là ended_in_session", () => {
  FidesSession.start()
  FidesSession.setSimulatedCall(true, false)
  expect(FidesSession.callBlock().state).toBe("active")
  FidesSession.setSimulatedCall(false, false)
  expect(FidesSession.callBlock().state).toBe("ended_in_session")
})
