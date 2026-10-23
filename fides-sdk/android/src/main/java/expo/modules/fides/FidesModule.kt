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
  private var simulated: Boolean? = null
  private var simulatedVoip: Boolean = false

  override fun definition() = ModuleDefinition {
    Name("Fides")
    Events("onCallStateChanged")

    Function("getCallState") {
      currentState()
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

  private fun currentState(): Map<String, Any?> {
    val inCall = telephonyInCall()
    if (inCall == true && startedAtMs == null) {
      startedAtMs = System.currentTimeMillis()
    }
    if (inCall == false) {
      startedAtMs = null
    }
    return mapOf(
      "in_call" to inCall,
      "call_started_at_ms" to startedAtMs,
      "voip_suspected" to voipSuspected(),
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
        if (inCall && startedAtMs == null) {
          startedAtMs = System.currentTimeMillis()
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
