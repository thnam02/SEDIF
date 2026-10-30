import assert from "node:assert/strict"
import test from "node:test"

import { maskAccount, movementTitle, parseTransferDraft, showAccount, when } from "./format.ts"

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
  assert.equal(showAccount("ZCAP400000000001"), "0902 401 001")
  assert.equal(showAccount("ZMULEDEMO0000001"), "0902 003 001")
  assert.equal(maskAccount("A0051289"), "•••• 1289")
  const tails = ["A0119434", "ZCAP400000000001", "ZGUIDE4000000001", "ZMULEDEMO0000001", "ZMED230000000001", "ZMED220000000001", "ZLANB40000000001", "ZLANB40000000002"].map((id) => maskAccount(id))
  assert.equal(new Set(tails).size, tails.length)
})

test("pasted text yields an account and the BVC bank", () => {
  assert.deepEqual(parseTransferDraft("STK ZVAY000000000001 BVC bank"), { account: "ZVAY000000000001", bank: "BVC bank" })
})
