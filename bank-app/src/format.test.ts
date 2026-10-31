import assert from "node:assert/strict"
import test from "node:test"

import { DEMO_CLOCK, groupAccount, isHeld, maskAccount, movementTitle, parseTransferDraft, presentHistory, presentStamp, reasonLine, showAccount, when } from "./format.ts"

test("when shows the Ho Chi Minh clock", () => {
  const stamp = when("2026-09-18T11:03:58+00:00")
  assert.equal(stamp.time, "18:03")
  assert.equal(stamp.date, "18/09/2026")
})

test("movement titles use Vietnamese, not raw codes", () => {
  assert.equal(movementTitle({ tx_type: "transfer", direction: "out", counterparty_name: "An" }), "Chuyển tiền tới An")
  assert.equal(movementTitle({ tx_type: "transfer", direction: "in", counterparty_name: "Ba" }), "Nhận tiền từ Ba")
  assert.equal(movementTitle({ tx_type: "savings_liquidation", direction: "in" }), "Tất toán sổ tiết kiệm")
  assert.equal(movementTitle({ tx_type: "quick_loan_disbursement", direction: "in" }), "Giải ngân khoản vay")
  assert.equal(movementTitle({ tx_type: "limit_raise", direction: "in" }), "Nâng hạn mức")
  assert.equal(movementTitle({ tx_type: "credit_card_cash_advance", direction: "in" }), "Rút tiền mặt thẻ tín dụng")
  assert.equal(movementTitle({ tx_type: "transfer", direction: "out" }), "Chuyển tiền")
})

test("demo payees show a numeric account and do not share a tail", () => {
  assert.equal(groupAccount("0902401001"), "0902 401 001")
  assert.equal(groupAccount("0060289"), "0060 289")
  assert.equal(showAccount("ZCAP400000000001"), "0902 401 001")
  assert.equal(showAccount("ZMULEDEMO0000001"), "0902 003 001")
  assert.equal(showAccount("A0002845"), "1903 002 845")
  assert.equal(showAccount("A0060289"), "1903 060 289")
  assert.equal(maskAccount("A0051289"), "•••• 1289")
  const tails = ["A0119434", "ZCAP400000000001", "ZGUIDE4000000001", "ZMULEDEMO0000001", "ZMED230000000001", "ZMED220000000001", "ZLANB40000000001", "ZLANB40000000002"].map((id) => maskAccount(id))
  assert.equal(new Set(tails).size, tails.length)
})

test("reason lines share one sentence style", () => {
  assert.equal(reasonLine("Giao dịch khác thường so với lịch sử của bạn"), "Giao dịch khác thường so với lịch sử của bạn.")
  assert.equal(reasonLine("Màn hình đang bị ghi hoặc chiếu."), "Màn hình đang bị ghi hoặc chiếu.")
  assert.equal(reasonLine("quét mã QR"), "Quét mã QR.")
})

test("history dates shift by the same number of days and keep real gaps", () => {
  const now = new Date(2026, 9, 31, 18, 0, 0)
  const clock = new Date(2026, 8, 21)
  assert.equal(DEMO_CLOCK.getFullYear(), 2026)
  assert.equal(DEMO_CLOCK.getMonth(), 8)
  assert.equal(DEMO_CLOCK.getDate(), 21)
  const source = [
    { tx_id: "older", ts: "2026-09-16T15:00:34+07:00" },
    { tx_id: "close", ts: "2026-09-21T08:48:52+07:00" },
    { tx_id: "send", ts: "2026-09-21T08:50:08+07:00" },
  ]
  const rows = presentHistory(source, now, clock)
  const shown = rows.map((row) => when(row.ts))
  assert.deepEqual(shown.map((item) => item.time), ["15:00", "08:48", "08:50"])
  assert.deepEqual(shown.map((item) => item.date), ["26/10/2026", "31/10/2026", "31/10/2026"])
  const reversed = presentHistory([...source].reverse(), now, clock)
  const byId = (list: typeof rows) => Object.fromEntries(list.map((row) => [row.tx_id, row.ts]))
  assert.deepEqual(byId(reversed), byId(rows))
  assert.equal(presentStamp("2026-09-21T21:41:15+07:00", now, clock), "2026-10-31T21:41:15+07:00")
  assert.equal(isHeld("held"), true)
  assert.equal(isHeld("done"), false)
  assert.equal(isHeld(undefined), false)
})

test("pasted text yields an account and the BVC bank", () => {
  assert.deepEqual(parseTransferDraft("STK ZVAY000000000001 BVC bank"), { account: "ZVAY000000000001", bank: "BVC bank" })
})
