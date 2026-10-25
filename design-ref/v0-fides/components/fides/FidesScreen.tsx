'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, Phone, ShieldCheck, UserRound, X } from 'lucide-react'
import type { FidesMock } from '@/lib/mock'
import { copy } from '@/lib/copy.vi'

type Props = { data: FidesMock; largeText: boolean; onRespond: (response: string) => void }

const buttonClass = 'flex min-h-11 items-center justify-center rounded-xl px-4 text-sm font-semibold transition-colors'

function Transaction({ data }: { data: FidesMock }) {
  return <div className='flex flex-col gap-1 rounded-2xl bg-mint p-4'><span className='text-xs text-deep/70'>{data.channel}</span><div className='flex items-center justify-between gap-3'><span className='font-semibold text-navy'>{data.recipient}</span><span className='font-bold text-deep'>{data.amount}</span></div></div>
}

function Countdown({ data, largeText }: { data: FidesMock; largeText: boolean }) {
  const [seconds, setSeconds] = useState(data.countdownSeconds ?? 0)
  useEffect(() => { if (seconds <= 0) return; const timer = window.setInterval(() => setSeconds((value) => Math.max(0, value - 1)), 1000); return () => window.clearInterval(timer) }, [seconds])
  return <div className='flex flex-col items-center gap-2 rounded-2xl bg-mint p-6 text-center'><Clock3 className='text-deep' /><span className={`font-bold text-deep ${largeText ? 'text-6xl' : 'text-5xl'}`}>{seconds}s</span><p className='text-sm text-navy'>{copy.wait.body}</p></div>
}

export function FidesScreen({ data, largeText, onRespond }: Props) {
  const titleSize = largeText ? 'text-2xl' : 'text-xl'
  const bodySize = largeText ? 'text-lg' : 'text-sm'
  const respond = (value: string) => onRespond(value)
  if (data.level === 'short_wait' || data.level === 'countdown') return <div className='flex flex-col gap-5'><h2 className={`font-bold text-navy ${titleSize}`}>{copy.wait.title}</h2><Countdown data={data} largeText={largeText} /><button className={`${buttonClass} bg-deep text-white`} onClick={() => respond('continue')}>{copy.continue}</button></div>
  if (data.level === 'held') return <div className='flex flex-col gap-5'><ShieldCheck className='text-aqua' size={42} /><h2 className={`font-bold text-navy ${titleSize}`}>{copy.held.title}</h2><p className={`text-navy ${bodySize}`}>{copy.held.body}</p><Transaction data={data} /><p className='text-sm font-semibold text-warn'>Dự kiến xử lý: {data.holdUntil}</p><button className={`${buttonClass} border border-deep text-deep`} onClick={() => respond('close')}>{copy.close}</button></div>
  if (data.level === 'high' || data.level === 'hangup_required') return <div className='flex flex-col gap-5'><AlertTriangle className='text-danger' size={42} /><h2 className={`font-bold text-navy ${titleSize}`}>{copy.high.title}</h2><p className={`text-navy ${bodySize}`}>{copy.high.body}</p><Transaction data={data} /><div className='flex gap-3'><button className={`${buttonClass} flex-1 bg-danger text-white`} onClick={() => respond('call')}><Phone size={17} />{copy.high.call}</button><button className={`${buttonClass} flex-1 border border-deep text-deep`} onClick={() => respond('close')}>{copy.high.close}</button></div></div>
  if (data.level === 'guardian_pending') return <div className='flex flex-col gap-5'><UserRound className='text-deep' size={42} /><h2 className={`font-bold text-navy ${titleSize}`}>{copy.guardian.title}</h2><p className={`text-navy ${bodySize}`}>{copy.guardian.body}</p><Transaction data={data} /><button className={`${buttonClass} bg-deep text-white`} onClick={() => respond('close')}>{copy.guardian.close}</button></div>
  if (data.level === 'show_chain') return <div className='flex flex-col gap-5'><h2 className={`font-bold text-navy ${titleSize}`}>{copy.chain.title}</h2><p className={`text-navy ${bodySize}`}>{copy.chain.body}</p><Transaction data={data} /><div className='flex flex-col gap-2 rounded-2xl border border-aqua p-4 text-sm text-navy'><span>1. Người gửi đã được xác minh</span><span>2. Nguồn tiền hợp lệ</span><span>3. Người nhận mới</span></div><button className={`${buttonClass} bg-deep text-white`} onClick={() => respond('confirm')}>{copy.chain.confirm}</button></div>
  if (data.level === 'medium' || data.level === 'pause_and_verify') return <div className='flex flex-col gap-5'><AlertTriangle className='text-warn' size={42} /><h2 className={`font-bold text-navy ${titleSize}`}>{copy.medium.title}</h2><p className={`text-navy ${bodySize}`}>{copy.medium.body}</p><Transaction data={data} /><div className='flex gap-3'><button className={`${buttonClass} flex-1 bg-deep text-white`} onClick={() => respond('verify')}>{copy.medium.verify}</button><button className={`${buttonClass} flex-1 border border-deep text-deep`} onClick={() => respond('cancel')}>{copy.medium.cancel}</button></div></div>
  return <div className='flex flex-col gap-5'><CheckCircle2 className='text-aqua' size={42} /><h2 className={`font-bold text-navy ${titleSize}`}>{copy.info.title}</h2><p className={`text-navy ${bodySize}`}>{copy.info.body}</p><Transaction data={data} /><div className='flex gap-3'><button className={`${buttonClass} flex-1 bg-deep text-white`} onClick={() => respond('recognized')}>{copy.info.yes}</button><button className={`${buttonClass} flex-1 border border-danger text-danger`} onClick={() => respond('unrecognized')}>{copy.info.no}</button></div></div>
}
