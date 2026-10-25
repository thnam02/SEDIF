import ExpoModulesCore
import CallKit

/// Delegate giữ bởi module. CXCallObserver chỉ giữ weak reference.
final class FidesCallDelegate: NSObject, CXCallObserverDelegate {
  var onChange: ((CXCall) -> Void)?

  func callObserver(_ callObserver: CXCallObserver, callChanged call: CXCall) {
    onChange?(call)
  }
}

private struct SeenCall {
  var direction: String
  var startedAtMs: Double
  var reported: Bool
}

public class FidesModule: Module {
  private let observer = CXCallObserver()
  private let delegate = FidesCallDelegate()
  private var seen: [UUID: SeenCall] = [:]
  private var startedAtMs: Double?
  private var lastInCall = false
  private var lastDirection: String?
  private var simulated: Bool?
  private var simulatedVoip = false

  public func definition() -> ModuleDefinition {
    Name("Fides")
    Events("onCallStateChanged")

    Function("getCallState") { () -> [String: Any] in
      self.snapshot()
    }

    Function("setSimulatedCall") { (active: Bool, voip: Bool) in
      self.applySimulation(active: active, voip: voip)
    }

    Function("beginSession") {
      self.beginSession()
    }

    Function("reconcile") { () -> [String: Any] in
      self.reconcile()
    }

    OnStartObserving {
      self.delegate.onChange = { [weak self] call in
        self?.handle(call)
      }
      self.observer.setDelegate(self.delegate, queue: nil)
    }

    OnStopObserving {
      self.delegate.onChange = nil
      self.observer.setDelegate(nil, queue: nil)
    }
  }

  /// Cuộc chưa hasEnded: đang đổ chuông hoặc đã nối máy. Không đọc số.
  private func activeCalls() -> [CXCall] {
    observer.calls.filter { !$0.hasEnded }
  }

  private func nowMs() -> Double {
    Date().timeIntervalSince1970 * 1000
  }

  /// Chỉ ghi UUID khi còn cuộc sống. Cuộc đã kết thúc mà chưa từng thấy thì bỏ qua.
  private func remember(_ call: CXCall) {
    if seen[call.uuid] != nil {
      return
    }
    if call.hasEnded {
      return
    }
    seen[call.uuid] = SeenCall(
      direction: call.isOutgoing ? "outbound" : "inbound",
      startedAtMs: nowMs(),
      reported: false
    )
  }

  private func rememberCurrent() {
    for call in observer.calls {
      remember(call)
    }
  }

  private func beginSession() {
    seen.removeAll()
    rememberCurrent()
    let active = activeCalls()
    lastInCall = !active.isEmpty
    if lastInCall && startedAtMs == nil {
      startedAtMs = seen.values.map(\.startedAtMs).min() ?? nowMs()
    }
  }

  /// So UUID đã thấy trong phiên với CXCallObserver.calls lúc về foreground.
  private func reconcile() -> [String: Any] {
    if simulated != nil {
      return snapshot()
    }
    let calls = observer.calls
    rememberCurrent()
    let live = Set(calls.map(\.uuid))
    var picked: SeenCall?
    for uuid in seen.keys.sorted(by: { $0.uuidString < $1.uuidString }) {
      guard var info = seen[uuid], !info.reported else {
        continue
      }
      let listed = calls.first { $0.uuid == uuid }
      let gone = !live.contains(uuid) || listed?.hasEnded == true
      if !gone {
        continue
      }
      info.reported = true
      seen[uuid] = info
      if picked == nil || info.startedAtMs >= (picked?.startedAtMs ?? 0) {
        picked = info
      }
    }
    let active = calls.filter { !$0.hasEnded }
    let inCall = !active.isEmpty
    if let picked {
      lastDirection = picked.direction
      let started = picked.startedAtMs
      lastInCall = inCall
      if !inCall {
        startedAtMs = nil
      }
      let ended: [String: Any] = [
        "in_call": inCall,
        "call_started_at_ms": value(started),
        "voip_suspected": false,
        "call_direction": value(picked.direction),
        "phase": "ended",
      ]
      sendEvent("onCallStateChanged", ended)
      return ended
    }
    lastInCall = inCall
    return snapshot()
  }

  private func direction(of calls: [CXCall], changed: CXCall?) -> String? {
    if let current = calls.first(where: { $0.hasConnected }) ?? calls.first {
      return current.isOutgoing ? "outbound" : "inbound"
    }
    if let changed, changed.hasEnded {
      return changed.isOutgoing ? "outbound" : "inbound"
    }
    return lastDirection
  }

  private func value(_ number: Double?) -> Any {
    if let number {
      return number
    }
    return NSNull()
  }

  private func value(_ text: String?) -> Any {
    if let text {
      return text
    }
    return NSNull()
  }

  private func applySimulation(active: Bool, voip: Bool) {
    let was = simulated == true
    let started = startedAtMs
    simulated = active
    simulatedVoip = voip
    if active && startedAtMs == nil {
      startedAtMs = Date().timeIntervalSince1970 * 1000
    }
    let phase: String? = active && !was ? "started" : (!active && was ? "ended" : nil)
    if !active {
      startedAtMs = nil
    }
    guard let phase else {
      return
    }
    sendEvent("onCallStateChanged", [
      "in_call": active,
      "call_started_at_ms": value(startedAtMs ?? started),
      "voip_suspected": voip,
      "call_direction": NSNull(),
      "phase": phase,
    ])
  }

  private func snapshot() -> [String: Any] {
    if let simulated {
      return [
        "in_call": simulated,
        "call_started_at_ms": value(startedAtMs),
        "voip_suspected": simulatedVoip,
        "call_direction": NSNull(),
      ]
    }
    rememberCurrent()
    let active = activeCalls()
    let inCall = !active.isEmpty
    if inCall && startedAtMs == nil {
      startedAtMs = Date().timeIntervalSince1970 * 1000
    }
    if let direction = direction(of: active, changed: nil) {
      lastDirection = direction
    }
    let started = startedAtMs
    lastInCall = inCall
    if !inCall {
      startedAtMs = nil
    }
    return [
      "in_call": inCall,
      "call_started_at_ms": value(inCall ? started : nil),
      "voip_suspected": false,
      "call_direction": value(inCall ? lastDirection : nil),
    ]
  }

  private func handle(_ call: CXCall) {
    if simulated != nil {
      return
    }
    remember(call)
    let active = activeCalls()
    let inCall = !active.isEmpty
    if inCall && startedAtMs == nil {
      startedAtMs = Date().timeIntervalSince1970 * 1000
    }
    if let direction = direction(of: active, changed: call) {
      lastDirection = direction
    }
    let started = startedAtMs
    let phase: String? = inCall && !lastInCall ? "started" : (!inCall && lastInCall ? "ended" : nil)
    lastInCall = inCall
    if !inCall {
      startedAtMs = nil
    }
    if phase == "ended" {
      let live = Set(active.map(\.uuid))
      for uuid in seen.keys {
        let listed = observer.calls.first { $0.uuid == uuid }
        if !live.contains(uuid) || listed?.hasEnded == true {
          seen[uuid]?.reported = true
        }
      }
    }
    guard let phase else {
      return
    }
    sendEvent("onCallStateChanged", [
      "in_call": inCall,
      "call_started_at_ms": value(started),
      "voip_suspected": false,
      "call_direction": value(lastDirection),
      "phase": phase,
    ])
  }
}
