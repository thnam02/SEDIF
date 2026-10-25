package expo.modules.fides

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioManager
import android.os.Build
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.util.concurrent.Executors

class FidesModule : Module() {
  private val executor = Executors.newSingleThreadExecutor()
  private var callback: TelephonyCallback? = null
  private var lastInCall: Boolean? = null
  private var startedAtMs: Long? = null
  private var seenStartedAt: Long? = null
  private var sawCellular = false
  private var sawVoip = false
  private var reportedEnded = false
  private var simulated: Boolean? = null
  private var simulatedVoip: Boolean = false

  override fun definition() = ModuleDefinition {
    Name("Fides")
    Events("onCallStateChanged")

    Function("getCallState") {
      currentState()
    }

    Function("beginSession") {
      beginSession()
    }

    Function("reconcile") {
      reconcile()
    }

    Function("setSimulatedCall") { active: Boolean, voip: Boolean ->
      val was = simulated == true
      val started = startedAtMs
      simulated = active
      simulatedVoip = voip
      if (active && startedAtMs == null) {
        startedAtMs = System.currentTimeMillis()
      }
      val phase = when {
        active && !was -> "started"
        !active && was -> "ended"
        else -> null
      }
      if (!active) {
        startedAtMs = null
      }
      if (phase != null) {
        sendEvent(
          "onCallStateChanged",
          mapOf(
            "in_call" to active,
            "call_started_at_ms" to (startedAtMs ?: started),
            "voip_suspected" to voip,
            "phase" to phase,
          ),
        )
      }
    }

    OnStartObserving {
      listen()
    }

    OnStopObserving {
      stop()
    }
  }

  private fun context(): Context = appContext.reactContext ?: throw IllegalStateException("thiếu context")

  private fun permitted(): Boolean {
    return ContextCompat.checkSelfPermission(context(), Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED
  }

  private fun voipSuspected(): Boolean {
    if (simulated != null) {
      return simulatedVoip
    }
    val audio = context().getSystemService(Context.AUDIO_SERVICE) as AudioManager
    return audio.mode == AudioManager.MODE_IN_COMMUNICATION
  }

  private fun telephonyInCall(): Boolean? {
    if (simulated != null) {
      return simulated
    }
    if (!permitted() || Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
      return null
    }
    val telephony = context().getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
    return when (telephony.callState) {
      TelephonyManager.CALL_STATE_OFFHOOK, TelephonyManager.CALL_STATE_RINGING -> true
      else -> false
    }
  }

  private fun noteLive(inCall: Boolean?, voip: Boolean) {
    if (inCall == true) {
      sawCellular = true
      reportedEnded = false
      if (seenStartedAt == null) {
        seenStartedAt = System.currentTimeMillis()
      }
      if (startedAtMs == null) {
        startedAtMs = seenStartedAt
      }
    }
    if (voip) {
      sawVoip = true
    }
  }

  private fun beginSession() {
    reportedEnded = false
    sawCellular = false
    sawVoip = false
    seenStartedAt = null
    val inCall = telephonyInCall()
    val voip = voipSuspected()
    noteLive(inCall, voip)
    lastInCall = inCall
  }

  /** Đọc lại TelephonyManager và AudioManager khi app về foreground. Không xin quyền. */
  private fun reconcile(): Map<String, Any?> {
    if (simulated != null) {
      return currentState()
    }
    val inCall = telephonyInCall()
    val voip = voipSuspected()
    val cellularGone = sawCellular && inCall == false
    val voipGone = sawVoip && !voip && inCall != true
    if ((cellularGone || voipGone) && !reportedEnded) {
      reportedEnded = true
      val started = seenStartedAt ?: startedAtMs
      if (inCall != true) {
        sawCellular = false
        startedAtMs = null
        seenStartedAt = null
        lastInCall = false
      }
      if (!voip) {
        sawVoip = false
      }
      val ended: Map<String, Any?> = mapOf(
        "in_call" to (inCall == true),
        "call_started_at_ms" to started,
        "voip_suspected" to voip,
        "call_direction" to null as Any?,
        "phase" to "ended",
      )
      sendEvent("onCallStateChanged", ended)
      return ended
    }
    noteLive(inCall, voip)
    lastInCall = inCall
    return currentState()
  }

  private fun currentState(): Map<String, Any?> {
    val inCall = telephonyInCall()
    val voip = voipSuspected()
    noteLive(inCall, voip)
    if (inCall == false) {
      startedAtMs = null
    }
    return mapOf(
      "in_call" to inCall,
      "call_started_at_ms" to startedAtMs,
      "voip_suspected" to voip,
    )
  }

  private fun listen() {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || !permitted() || callback != null) {
      return
    }
    val telephony = context().getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
    val created = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
      override fun onCallStateChanged(state: Int) {
        val inCall = state == TelephonyManager.CALL_STATE_OFFHOOK || state == TelephonyManager.CALL_STATE_RINGING
        val previous = lastInCall
        lastInCall = inCall
        if (inCall) {
          sawCellular = true
          reportedEnded = false
          if (seenStartedAt == null) {
            seenStartedAt = System.currentTimeMillis()
          }
        }
        if (inCall && startedAtMs == null) {
          startedAtMs = seenStartedAt
        }
        val started = startedAtMs
        val phase = when {
          inCall && previous != true -> "started"
          !inCall && previous == true -> "ended"
          else -> null
        }
        if (!inCall) {
          startedAtMs = null
        }
        if (phase == "ended") {
          reportedEnded = true
        }
        if (phase != null) {
          sendEvent(
            "onCallStateChanged",
            mapOf(
              "in_call" to inCall,
              "call_started_at_ms" to (startedAtMs ?: started),
              "voip_suspected" to voipSuspected(),
              "phase" to phase,
            ),
          )
        }
      }
    }
    callback = created
    telephony.registerTelephonyCallback(executor, created)
  }

  private fun stop() {
    val current = callback ?: return
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
      val telephony = context().getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
      telephony.unregisterTelephonyCallback(current)
    }
    callback = null
  }
}
