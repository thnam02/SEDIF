export type FidesLevel = 'info' | 'banner' | 'simo_banner' | 'medium' | 'pause_and_verify' | 'show_chain' | 'guardian_pending' | 'short_wait' | 'high' | 'hangup_required' | 'countdown' | 'held'

export type FidesMock = { id: string; level: FidesLevel; amount: string; recipient: string; channel: string; countdownSeconds?: number; holdUntil?: string }
export const fidesMocks: FidesMock[] = [
  { id: 'info', level: 'info', amount: '+500.000 đ', recipient: 'Trần Bình Minh', channel: 'Chuyển khoản' },
  { id: 'medium', level: 'medium', amount: '12.500.000 đ', recipient: 'Nguyễn Minh Anh', channel: 'Chuyển khoản' },
  { id: 'chain', level: 'show_chain', amount: '8.000.000 đ', recipient: 'Công ty ABC', channel: 'Thanh toán hóa đơn' },
  { id: 'guardian', level: 'guardian_pending', amount: '2.500.000 đ', recipient: 'Lê Hoàng', channel: 'Chuyển tiền' },
  { id: 'wait', level: 'short_wait', amount: '5.000.000 đ', recipient: 'Phạm Lan', channel: 'Chuyển khoản', countdownSeconds: 12 },
  { id: 'high', level: 'high', amount: '45.000.000 đ', recipient: 'Tài khoản mới', channel: 'Chuyển khoản' },
  { id: 'countdown', level: 'countdown', amount: '15.000.000 đ', recipient: 'Đỗ Thanh', channel: 'Chuyển khoản', countdownSeconds: 8 },
  { id: 'held', level: 'held', amount: '20.000.000 đ', recipient: 'Võ Nam', channel: 'Chuyển khoản', holdUntil: '13:52 hôm nay' },
]

export const bankTabs = [
  { id: 'accounts', label: 'Tài khoản demo' }, { id: 'home', label: 'Trang chủ' },
  { id: 'recipient', label: 'Chọn người nhận' }, { id: 'amount', label: 'Nhập số tiền' },
  { id: 'confirm', label: 'Xác nhận' }, { id: 'success', label: 'Chuyển thành công' },
  { id: 'savings', label: 'Tiết kiệm' }, { id: 'loan', label: 'Vay nhanh' },
  { id: 'limits', label: 'Hạn mức' }, { id: 'cash', label: 'Rút tiền thẻ' },
  { id: 'guardian', label: 'Người thân' }, { id: 'stats', label: 'Thống kê' }, { id: 'history', label: 'Lịch sử' },
] as const
export type BankTab = typeof bankTabs[number]['id']

export const transactions = [
  { name: 'Lương tháng 06', meta: 'Công ty Sao Việt', amount: '+25.000.000 đ', time: '09:12', date: '18/06', incoming: true },
  { name: 'Trần Bình Minh', meta: 'Chuyển khoản', amount: '-500.000 đ', time: '13:45', date: '13/06', incoming: false },
  { name: 'Cửa hàng tiện lợi', meta: 'Thanh toán QR', amount: '-285.000 đ', time: '19:20', date: '12/06', incoming: false },
]
