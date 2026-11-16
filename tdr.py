"""
Time domain reflectometry (TDR) of a coax cable from S11 measurements.

The reflection coefficient is measured on a harmonic frequency grid
    f(k) = k · Δf,  k = 1 .. N
which, together with a point derived for 0 Hz, is the spectrum of a real
signal.  Its inverse Fourier transform is the impulse response of the cable:
the reflection seen at each round-trip time, i.e. at each distance
    d = VF · c · t / 2
A positive peak means the impedance rises at that distance (up to an open
end), a negative one that it drops (down to a short).  The running sum of the
impulse response is the step response, which gives the approximate impedance
along the cable: Z = 50 · (1 + ρ) / (1 - ρ).

The frequency step sets the range (VF · c / (2 · Δf)) and the highest
frequency sets the resolution, so the grid is planned from the maximum cable
length and the maximum frequency.  The NanoVNA measures a limited number of
points at a time (the configured sweep points), so a finer grid is stitched
from several consecutive scans.
"""
import math
import numpy as np
from cable import SPEED_OF_LIGHT

REFERENCE_IMPEDANCE = 50.0
# Default points measured by one scan (supported by all devices)
SEGMENT_POINTS = 101
# Most scans stitched into one measurement
MAX_SEGMENTS = 5
# Lowest frequency of the NanoVNA, which is also the smallest usable step
MIN_STEP_HZ = 50000
# The range is planned this much longer than the maximum cable length
RANGE_MARGIN = 1.25
# The last part of the transformed range is taken as negative time (the part
# of a reflection at the connection point that falls before distance zero)
NEGATIVE_TIME_FRACTION = 0.15
# Kaiser window parameter: side lobes about 44 dB below a reflection
WINDOW_BETA = 6.0
# The transform is interpolated to at least this many samples per grid point
OVERSAMPLING = 16
# Kaiser window parameter used to find the value at 0 Hz: side lobes about
# 95 dB down
DC_WINDOW_BETA = 13.0
# Part of the negative time left out next to its ends when finding the value
# at 0 Hz, in widths of a single reflection
DC_MARGIN_WIDTHS = 2.0
# Reflections weaker than this are not reported
MIN_EVENT_RHO = 0.03
# ... nor are reflections below this multiple of the median response
NOISE_FACTOR = 4.0
# The reflection that changes the step response the most is taken as the cable
# end when the change is at least this large
END_RHO = 0.3
# The change is measured over this many reflection widths after the
# reflection, or this fraction of the range when longer
END_TAIL_WIDTHS = 8.0
END_TAIL_FRACTION = 0.1
# Size wording of a reflection
SMALL_RHO = 0.1
LARGE_RHO = 0.3
# A cable end beyond this fraction of the range may be folded back from
# beyond the range
RANGE_WARNING_FRACTION = 0.9
# A cable can't reflect more than it receives: measured reflections above
# this (allowing for noise) mean an inaccurate calibration
PASSIVE_LIMIT = 1.05
# Rows of the impedance table (plus the row at distance zero)
PROFILE_ROWS = 20
# Impedances outside these limits are reported as beyond the limit
MAX_IMPEDANCE = 2000.0
MIN_IMPEDANCE = 1.0


def parse_max_length(text):
    """ Parses the maximum cable length in metres, which must be positive. """
    if text is None or text.strip() == "":
        raise Exception("Maximum cable length is missing")
    try:
        value = float(text)
    except ValueError:
        raise Exception("Invalid maximum cable length: " + text.strip())
    if not math.isfinite(value) or value <= 0.0:
        raise Exception("Invalid maximum cable length: " + text.strip() + " (must be greater than 0)")
    return value


def plan_sweep(max_length_m, max_frequency_hz, velocity_factor, max_segment_points=SEGMENT_POINTS):
    """
    Chooses the harmonic frequency grid: the step is small enough for the
    range to cover the maximum cable length (with a margin), and the grid
    reaches as close to the maximum frequency as the scans allow, for the best
    resolution.  Each scan measures only as many points as needed (but at least
    SEGMENT_POINTS), since more points take longer without improving the
    resolution.

    :param max_segment_points: Most points one scan may measure (the
        configured sweep points)
    :return: The frequency step in Hz (an integer), the number of scans and
        the number of points of each scan
    """
    needed_step_hz = velocity_factor * SPEED_OF_LIGHT / (2.0 * RANGE_MARGIN * max_length_m)
    if needed_step_hz < MIN_STEP_HZ:
        longest_m = velocity_factor * SPEED_OF_LIGHT / (2.0 * RANGE_MARGIN * MIN_STEP_HZ)
        raise Exception("Maximum cable length is too long. The longest measurable "
            "length of this cable type is {:.0f} m.".format(math.floor(longest_m)))
    # Points the whole grid needs to reach the maximum frequency
    needed_points = int(math.ceil(max_frequency_hz / needed_step_hz))
    min_segment_points = min(SEGMENT_POINTS, max_segment_points)
    best = None
    for segments in range(1, MAX_SEGMENTS + 1):
        segment_points = min(max_segment_points,
            max(min_segment_points, int(math.ceil(needed_points / segments))),
            # No more points than fit above the smallest step
            int(max_frequency_hz // (MIN_STEP_HZ * segments)))
        if segment_points < min_segment_points:
            break
        points = segment_points * segments
        step_hz = int(math.floor(min(needed_step_hz, max_frequency_hz / points)))
        if step_hz < MIN_STEP_HZ:
            break
        # More scans only when they reach a higher frequency
        if best is None or points * step_hz > 1.001 * best[0]:
            best = (points * step_hz, step_hz, segments, segment_points)
    if best is None:
        raise Exception("Maximum frequency is too low. It must be at least {:.2f} MHz.".format(
            min_segment_points * MIN_STEP_HZ / 1000000.0))
    return best[1], best[2], best[3]


def calibration_warnings(calibrated_range, start_hz, end_hz):
    """
    Warns when the measured frequencies go outside the calibrated range.

    :param calibrated_range: (start, end) of the recalled calibration in Hz,
        or None when the device doesn't report it
    :param start_hz: Lowest measured frequency (the grid step)
    :param end_hz: Highest measured frequency
    """
    if calibrated_range is None:
        return []
    cal_start_hz, cal_end_hz = calibrated_range
    # Half a grid step of tolerance for frequencies rounded by the device
    if start_hz >= cal_start_hz - start_hz / 2.0 and end_hz <= cal_end_hz + start_hz / 2.0:
        return []
    return ["The calibration covers {:.3f} to {:.3f} MHz, but the measurement goes from "
        "{:.3f} to {:.3f} MHz. Outside the calibrated range the results are not reliable. "
        "Calibrate from about 0.05 MHz up to the maximum frequency, or lower the maximum "
        "frequency.".format(cal_start_hz / 1e6, cal_end_hz / 1e6, start_hz / 1e6, end_hz / 1e6)]


def segment_ranges(step_hz, segments, segment_points=SEGMENT_POINTS):
    """ The (start, end) frequency in Hz of each scan of the grid. """
    return [((segment_points * s + 1) * step_hz, segment_points * (s + 1) * step_hz)
        for s in range(segments)]


def _on_grid(frequencies, rcs, step_hz, point_count):
    """
    Returns the reflection coefficients at k · step, k = 1 .. point_count.
    Data measured on another grid (a firmware with a different number of
    sweep points) is interpolated.
    """
    if len(frequencies) != len(rcs):
        raise Exception("Length error")
    if len(frequencies) < 3:
        raise Exception("Not enough measured points")
    f = np.array(frequencies, dtype=float)
    rc = np.array(rcs, dtype=complex)
    grid = step_hz * np.arange(1, point_count + 1, dtype=float)
    if len(f) == point_count and np.all(np.abs(f - grid) < 0.01 * step_hz):
        return rc
    if np.any(np.diff(f) <= 0.0):
        raise Exception("Measured frequencies are not ascending")
    if f[0] > 1.5 * step_hz or f[-1] < grid[-1] - 0.5 * step_hz:
        raise Exception("Measured frequencies do not cover the requested range")
    return np.interp(grid, f, rc.real) + 1j * np.interp(grid, f, rc.imag)


def _dc_value(rc, size, negative):
    """
    Finds the reflection coefficient at 0 Hz, which can't be measured.  It
    adds the same amount to every sample of the impulse response, and the
    response must be zero before time zero (nothing reflects before the signal
    is sent), so it is the value that makes the negative time part zero.  A
    window with very low side lobes keeps the reflections themselves out of
    that part, and the median ignores a repeat echo falling into it.
    """
    n_points = len(rc)
    window = np.kaiser(2 * n_points + 1, DC_WINDOW_BETA)[n_points:]
    raw = np.fft.irfft(np.concatenate(([0.0], rc)) * window, n=size)
    # Stay clear of reflections at the connection point and at the range end
    margin = int(math.ceil(DC_MARGIN_WIDTHS * _half_width_samples(window, size)))
    quiet = raw[size - negative + margin:size - margin]
    if len(quiet) == 0:
        quiet = raw[size - negative // 2:size - negative // 2 + 1]
    return min(1.0, max(-1.0, float(-size * np.median(quiet))))


def _half_width_samples(window, size):
    """
    Width at half amplitude, in samples, of the response to a single ideal
    reflection, which is the transform of the window itself.
    """
    response = np.fft.irfft(window, n=size)
    response = response / response[0]
    below = np.nonzero(response[:size // 2] < 0.5)[0]
    if len(below) == 0:
        return float(size // 2)
    n = int(below[0])
    # Linear interpolation between the samples around the half amplitude
    fraction = (response[n - 1] - 0.5) / (response[n - 1] - response[n])
    return 2.0 * (n - 1 + fraction)


def _impedance(rho):
    return REFERENCE_IMPEDANCE * (1.0 + rho) / (1.0 - rho) if rho < 1.0 else math.inf


def _fmt_impedance(rho):
    z = _impedance(rho)
    if z > MAX_IMPEDANCE:
        return "more than {:.0f}".format(MAX_IMPEDANCE)
    if z < MIN_IMPEDANCE:
        return "less than {:.0f}".format(MIN_IMPEDANCE)
    if z < 10.0:
        return "{:.01f}".format(z)
    return "{:.0f}".format(z)


def _fmt(value):
    return "{:.02f}".format(value)


def _plateau(step, start, end):
    """ Median step response between two samples (in either order). """
    last = len(step) - 1
    start = min(max(int(round(start)), 0), last)
    end = min(max(int(round(end)), 0), last)
    if end < start:
        # No room between two reflections: take the point half way
        return float(step[(start + end) // 2])
    return float(np.median(step[start:end + 1]))


def _find_peaks(magnitude, threshold, first, min_distance):
    """
    Indexes of the local maxima above the threshold, from the given first
    index; of maxima closer than the minimum distance the stronger is kept.
    """
    middle = magnitude[1:-1]
    found = np.nonzero((middle > threshold) & (middle >= magnitude[:-2]) & (middle > magnitude[2:]))[0] + 1
    peaks = []
    for i in found:
        if i < first:
            continue
        if peaks and i - peaks[-1] < min_distance:
            if magnitude[i] > magnitude[peaks[-1]]:
                peaks[-1] = int(i)
        else:
            peaks.append(int(i))
    return peaks


def _describe(rho, at_connection, is_end, after_end):
    size = abs(rho)
    if is_end:
        if rho > 0:
            text = "cable end: open, a break, or a load such as an antenna"
        else:
            text = "cable end: short circuit, or a load shorted at low frequencies such as some antennas"
    else:
        if size < SMALL_RHO:
            text = "small"
        elif size < LARGE_RHO:
            text = "moderate"
        else:
            text = "large"
        text += " impedance rise" if rho > 0 else " impedance drop"
    if at_connection:
        text += ", at the connection point"
    if after_end:
        text += ", possibly a repeat echo because it lies beyond the main reflection"
    return text


def analyze_tdr(frequencies, rcs, velocity_factor, step_hz, point_count):
    """
    Builds the formatted (JSON-ready) TDR analysis.

    :param frequencies: Measured frequencies in Hz, ascending
    :param rcs: Complex reflection coefficients at those frequencies
    :param velocity_factor: Velocity factor of the cable, in (0, 1]
    :param step_hz: Step of the planned harmonic frequency grid
    :param point_count: Number of points of the planned grid
    """
    rc = _on_grid(frequencies, rcs, step_hz, point_count)
    n_points = len(rc)

    size = 1 << int(math.ceil(math.log2(OVERSAMPLING * (2 * n_points + 1))))
    negative = int(size * NEGATIVE_TIME_FRACTION)

    # Spectrum from 0 Hz up, tapered towards the highest frequency
    window = np.kaiser(2 * n_points + 1, WINDOW_BETA)[n_points:]
    spectrum = np.concatenate(([_dc_value(rc, size, negative)], rc)) * window
    window_sum = window[0] + 2.0 * float(np.sum(window[1:]))

    raw = np.fft.irfft(spectrum, n=size)
    # Put the negative time first: index i is the sample at time (i - negative)
    raw = np.concatenate((raw[size - negative:], raw[:size - negative]))
    # Scaled so that a single reflection peaks at its reflection coefficient
    impulse = raw * size / window_sum
    step = np.cumsum(raw)

    metres_per_sample = velocity_factor * SPEED_OF_LIGHT / (2.0 * step_hz * size)
    range_m = (len(raw) - 1 - negative) * metres_per_sample
    half_width = _half_width_samples(window, size)
    resolution_m = half_width * metres_per_sample
    # Distance from a peak beyond which the step response has settled
    settle = 1.5 * half_width

    magnitude = np.abs(impulse)
    threshold = max(MIN_EVENT_RHO, NOISE_FACTOR * float(np.median(magnitude)))
    peaks = _find_peaks(magnitude, threshold, max(1, negative - int(half_width / 2.0)), half_width)

    # Step response before each reflection
    befores = []
    for number, i in enumerate(peaks):
        if i - negative < half_width / 2.0:
            # Nothing but the calibrated port lies before the connection point
            befores.append(0.0)
        else:
            previous = peaks[number - 1] + settle if number > 0 else negative
            befores.append(_plateau(step, previous, i - settle))

    # The cable end is the reflection that moves the step response the most
    # over a stretch after it.  Cable loss spreads the reflection of a far end
    # into a long tail, which lowers its peak but not this change; the stretch
    # also spans the tail.  A short bump (a rise followed by a drop) within the
    # stretch changes it little.
    tail = max(END_TAIL_WIDTHS * half_width, END_TAIL_FRACTION * (len(step) - negative))
    end_peak = None
    end_after = None
    best_change = END_RHO
    for number, i in enumerate(peaks):
        after = _plateau(step, i + settle, i + settle + tail)
        change = abs(after - befores[number])
        if change >= best_change:
            best_change = change
            end_peak = i
            end_after = after

    events = []
    for number, i in enumerate(peaks):
        rho = float(impulse[i])
        at_connection = i - negative < half_width / 2.0
        rho_before = befores[number]
        if i == end_peak:
            # Includes the tail of a lossy end
            rho_after = end_after
        elif number + 1 < len(peaks):
            rho_after = _plateau(step, i + settle, peaks[number + 1] - settle)
        else:
            rho_after = _plateau(step, i + settle, i + 4.0 * half_width)
        after_end = end_peak is not None and i > end_peak
        events.append({
            "distance_m": _fmt(max(0, i - negative) * metres_per_sample),
            "reflection": "{:+.02f}".format(rho),
            "return_loss_db": "{:.01f}".format(-20.0 * math.log10(abs(rho))),
            # There is no meaningful impedance beyond the cable end
            "impedance_before": None if after_end else _fmt_impedance(rho_before),
            "impedance_after": None if after_end else _fmt_impedance(rho_after),
            "description": _describe(rho, at_connection, i == end_peak, after_end)
        })

    warnings = []
    too_large = np.nonzero(np.abs(rc) > PASSIVE_LIMIT)[0]
    if len(too_large) > 0:
        worst = int(too_large[np.argmax(np.abs(rc[too_large]))])
        warnings.append("At {} of the measured frequencies the reflection is larger than 1 "
            "(up to {:.2f} at {:.2f} MHz), which a cable can't cause. The calibration is "
            "probably inaccurate there, which distorts the impedance along the cable. "
            "Calibrate again with the calibration points closer together: over a "
            "narrower frequency range or with more sweep points.".format(
                len(too_large), abs(rc[worst]), step_hz * (worst + 1) / 1000000.0))
    end = {"found": False}
    profile_extent_m = range_m
    if end_peak is not None:
        end_distance_m = max(0, end_peak - negative) * metres_per_sample
        end = {
            "found": True,
            "distance_m": _fmt(end_distance_m),
            "state": "open" if impulse[end_peak] > 0 else "short",
            "description": events[peaks.index(end_peak)]["description"]
        }
        profile_extent_m = min(range_m, end_distance_m + max(3.0 * resolution_m, 0.1 * end_distance_m))
        if end_peak - negative < half_width / 2.0:
            warnings.append("The main reflection is at the connection point. "
                "Is a cable connected?")
        if end_distance_m > RANGE_WARNING_FRACTION * range_m:
            warnings.append("The main reflection is close to the end of the measured range. "
                "It may come from beyond the range and be shown at a wrong distance. "
                "Raise the maximum cable length and measure again.")
        if peaks[-1] > end_peak:
            warnings.append("Reflections listed beyond the main reflection are usually repeat "
                "echoes between earlier reflections, not real features of the cable.")
    elif peaks:
        warnings.append("No cable end was found. The far end is terminated, the cable is "
            "very lossy, or it is longer than the measured range.")
    else:
        warnings.append("No reflections were found. The cable is matched along the whole "
            "measured range, or it is longer than the measured range.")

    profile = []
    half_bin = profile_extent_m / PROFILE_ROWS / 2.0
    for row in range(PROFILE_ROWS + 1):
        distance_m = profile_extent_m * row / PROFILE_ROWS
        first = negative + max(0.0, distance_m - half_bin) / metres_per_sample
        last = negative + (distance_m + half_bin) / metres_per_sample
        profile.append({
            "distance_m": _fmt(distance_m),
            "impedance": _fmt_impedance(_plateau(step, first, last))
        })

    return {
        "velocity_factor": "{:.03f}".format(velocity_factor),
        "end": end,
        "events": events,
        "profile": profile,
        "range_m": _fmt(range_m),
        "resolution_m": _fmt(resolution_m),
        "step_frequency_mhz": "{:.03f}".format(step_hz / 1000000.0),
        "end_frequency_mhz": "{:.03f}".format(step_hz * n_points / 1000000.0),
        "point_count": n_points,
        "warnings": warnings
    }
