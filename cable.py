"""
Coax cable length measurement from an S11 sweep of a cable whose far end is
open or shorted.

The reflection coefficient of such a cable is approximately
    Γ(f) = ±A · exp(-j · 4π · f · τ)
where τ is the one-way delay of the cable.  The unwrapped phase of Γ is
therefore a straight line in frequency with slope -4π·τ.  A least-squares fit
over all measured points gives τ, the electrical length c·τ and the physical
length VF·c·τ.  The fit intercept (phase extrapolated to 0 Hz) tells whether
the far end is open (0°) or shorted (180°), and |Γ| gives the cable loss
(the signal passes the cable twice, so one-way loss is half the return loss).
"""
import math
import numpy as np

SPEED_OF_LIGHT = 299792458.0

# Below this total phase rotation over the sweep there is no usable cable signal
MIN_ROTATION_DEG = 5.0
# Below this total phase rotation the result is imprecise
SHORT_ROTATION_DEG = 180.0
# Above this fraction of the maximum measurable length the phase may have
# turned by more than 180° between two points, giving a wrong (too short) result
MAX_LENGTH_MARGIN = 0.8
# An open or shorted cable reflects strongly; below this mean |Γ| the far end
# is probably terminated or the cable very lossy
MIN_MEAN_GAMMA = 0.3
# RMS deviation of the phase from a straight line above which the far end is
# probably not a clean open or short
MAX_FIT_RMS_DEG = 20.0
# Points on each side used for the median |Γ| of a loss estimate
LOSS_HALF_WINDOW = 2


def parse_velocity_factor(text):
    """ Parses the velocity factor, which must be in the range (0, 1]. """
    if text is None or text.strip() == "":
        raise Exception("Velocity factor is missing")
    try:
        value = float(text)
    except ValueError:
        raise Exception("Invalid velocity factor: " + text.strip())
    if not math.isfinite(value) or value <= 0.0 or value > 1.0:
        raise Exception("Invalid velocity factor: " + text.strip() + " (must be greater than 0 and at most 1)")
    return value


def wrap_degrees(angle):
    """ Wraps an angle in degrees into the range (-180, 180]. """
    wrapped = math.fmod(angle, 360.0)
    if wrapped <= -180.0:
        wrapped += 360.0
    elif wrapped > 180.0:
        wrapped -= 360.0
    return wrapped


def far_end_state(phase_at_zero_deg):
    """ Classifies the far end from the phase extrapolated to 0 Hz. """
    magnitude = abs(wrap_degrees(phase_at_zero_deg))
    if magnitude < 45.0:
        return "open"
    if magnitude > 135.0:
        return "short"
    return "unclear (reactive load or adapter)"


def one_way_loss_db(gamma):
    """ One-way cable loss in dB from |Γ| of the open or shorted cable. """
    if gamma >= 1.0:
        return 0.0
    if gamma <= 0.0:
        return math.inf
    return -10.0 * math.log10(gamma)


def _fmt(value):
    if math.isinf(value):
        return "∞"
    return "{:.02f}".format(value)


def _fmt_mhz(frequency_hz):
    return "{:.03f}".format(frequency_hz / 1000000.0)


def measure_cable(frequencies, rcs, velocity_factor):
    """
    Builds the formatted (JSON-ready) cable measurement.

    :param frequencies: Measured frequencies in Hz, ascending
    :param rcs: Complex reflection coefficients at those frequencies
    :param velocity_factor: Velocity factor of the cable, in (0, 1]
    """
    if len(frequencies) != len(rcs):
        raise Exception("Length error")
    if len(frequencies) < 3:
        raise Exception("Not enough measured points")
    f = np.array(frequencies, dtype=float)
    rc = np.array(rcs, dtype=complex)

    phase = np.unwrap(np.angle(rc))
    slope, intercept = np.polyfit(f, phase, 1)
    rotation_deg = math.degrees(-slope * (f[-1] - f[0]))
    if rotation_deg < MIN_ROTATION_DEG:
        raise Exception("No phase rotation detected. Is a cable connected? "
            "For a very short cable raise the end frequency.")

    # One-way delay and lengths
    delay_s = -slope / (4.0 * math.pi)
    electrical_length_m = SPEED_OF_LIGHT * delay_s
    physical_length_m = velocity_factor * electrical_length_m
    # The phase may turn by at most 180° between two points
    max_step_hz = float(np.max(np.diff(f)))
    max_length_m = velocity_factor * SPEED_OF_LIGHT / (4.0 * max_step_hz)

    residual = phase - (slope * f + intercept)
    fit_rms_deg = math.degrees(math.sqrt(float(np.mean(residual ** 2))))
    phase_at_zero_deg = wrap_degrees(math.degrees(intercept))

    gammas = np.abs(rc)
    n = len(f)
    losses = []
    for i in [0, n // 2, n - 1]:
        window = gammas[max(0, i - LOSS_HALF_WINDOW):min(n, i + LOSS_HALF_WINDOW + 1)]
        losses.append({
            "frequency_mhz": _fmt_mhz(f[i]),
            "loss_db": _fmt(one_way_loss_db(float(np.median(window))))
        })

    warnings = []
    if physical_length_m > MAX_LENGTH_MARGIN * max_length_m:
        warnings.append("The cable may be longer than this frequency range can measure. "
            "Lower the end frequency or narrow the range and measure again.")
    if rotation_deg < SHORT_ROTATION_DEG:
        warnings.append("The cable is short for this frequency range. "
            "Raise the end frequency for better accuracy.")
    if float(np.mean(gammas)) < MIN_MEAN_GAMMA or fit_rms_deg > MAX_FIT_RMS_DEG:
        warnings.append("The far end looks terminated or the cable is very lossy. "
            "Leave the far end open or short it.")

    return {
        "velocity_factor": "{:.03f}".format(velocity_factor),
        "physical_length_m": _fmt(physical_length_m),
        "electrical_length_m": _fmt(electrical_length_m),
        "delay_ns": _fmt(delay_s * 1e9),
        "far_end": far_end_state(phase_at_zero_deg),
        "far_end_phase_deg": "{:.0f}".format(phase_at_zero_deg),
        "losses": losses,
        "max_length_m": _fmt(max_length_m),
        "fit_rms_deg": "{:.01f}".format(fit_rms_deg),
        "point_count": n,
        "warnings": warnings
    }
