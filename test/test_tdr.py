import cmath
import math
import unittest
import tdr
from cable import SPEED_OF_LIGHT

VF = 0.66


def grid(step_hz, points):
    return [float(step_hz * k) for k in range(1, points + 1)]


def round_trip(f, length_m, velocity_factor=VF):
    """ Phase factor of travelling the length there and back. """
    return cmath.exp(-1j * 4 * math.pi * f * length_m / (velocity_factor * SPEED_OF_LIGHT))


def cable_rcs(f_list, length_m, amplitude=1.0, short=False):
    """ Reflection coefficients of an ideal cable with an open or shorted far end. """
    sign = -1.0 if short else 1.0
    return [sign * amplitude * round_trip(f, length_m) for f in f_list]


def two_section_rcs(f_list, length1_m, z2, length2_m):
    """ A 50 ohm section followed by a section of impedance z2 with an open end. """
    rho = (z2 - 50.0) / (z2 + 50.0)
    result = []
    for f in f_list:
        end = round_trip(f, length2_m)
        junction = (rho + end) / (1.0 + rho * end)
        result.append(junction * round_trip(f, length1_m))
    return result


def coax_rcs(f_list, sections, load_rho, loss_db_per_m=0.28):
    """
    Reflection coefficients of cable sections with skin effect loss (in
    proportion to the square root of the frequency, given in dB/m at 300 MHz,
    0.28 is about RG-58).

    :param sections: (impedance, length in m) of each section from the port
    :param load_rho: Reflection coefficient at the far end
    """
    result = []
    for f in f_list:
        rho = load_rho
        for number in range(len(sections) - 1, -1, -1):
            z, length_m = sections[number]
            loss_db = 2.0 * length_m * loss_db_per_m * math.sqrt(f / 300e6)
            rho = rho * 10.0 ** (-loss_db / 20.0) * round_trip(f, length_m)
            z_before = sections[number - 1][0] if number > 0 else 50.0
            junction = (z - z_before) / (z + z_before)
            rho = (junction + rho) / (1.0 + junction * rho)
        result.append(rho)
    return result


def measure(rcs_function, max_length_m=20.0, max_frequency_hz=300e6):
    step_hz, segments, _ = tdr.plan_sweep(max_length_m, max_frequency_hz, VF)
    points = tdr.SEGMENT_POINTS * segments
    f = grid(step_hz, points)
    return tdr.analyze_tdr(f, rcs_function(f), VF, step_hz, points)


class TestTdr(unittest.TestCase):

    def test_parse_max_length(self):
        self.assertEqual(50.0, tdr.parse_max_length("50"))
        self.assertEqual(2.5, tdr.parse_max_length(" 2.5 "))
        for bad in [None, "", "  ", "0", "-3", "abc", "nan", "inf"]:
            with self.assertRaises(Exception):
                tdr.parse_max_length(bad)

    def test_plan_single_scan(self):
        # The needed step (3.96 MHz) is wider than 300 MHz / 101, so one scan
        # already reaches the maximum frequency
        step_hz, segments, _ = tdr.plan_sweep(20.0, 300e6, VF)
        self.assertEqual(1, segments)
        self.assertEqual(2970297, step_hz)

    def test_plan_more_scans_for_longer_cable(self):
        step_hz, segments, _ = tdr.plan_sweep(50.0, 300e6, VF)
        self.assertEqual(2, segments)
        self.assertEqual(1485148, step_hz)
        # The range covers the maximum length with the margin
        self.assertGreaterEqual(VF * SPEED_OF_LIGHT / (2.0 * step_hz), tdr.RANGE_MARGIN * 50.0)

    def test_plan_scan_limit(self):
        step_hz, segments, _ = tdr.plan_sweep(500.0, 300e6, VF)
        self.assertEqual(tdr.MAX_SEGMENTS, segments)
        self.assertGreaterEqual(VF * SPEED_OF_LIGHT / (2.0 * step_hz), tdr.RANGE_MARGIN * 500.0)
        self.assertLessEqual(step_hz * tdr.SEGMENT_POINTS * segments, 300e6)

    def test_plan_more_points_per_scan(self):
        # 5 scans of 101 points can't reach 300 MHz with the small step a
        # 500 m cable needs, 5 scans of 401 points can
        step_hz, segments, _ = tdr.plan_sweep(500.0, 300e6, VF)
        self.assertLess(step_hz * tdr.SEGMENT_POINTS * segments, 100e6)
        step_hz, segments, points = tdr.plan_sweep(500.0, 300e6, VF, 401)
        self.assertEqual(tdr.MAX_SEGMENTS, segments)
        self.assertLessEqual(points, 401)
        self.assertGreater(step_hz * points * segments, 0.99 * 300e6)
        self.assertGreaterEqual(VF * SPEED_OF_LIGHT / (2.0 * step_hz), tdr.RANGE_MARGIN * 500.0)
        # The 50 m cable that needs two scans of 101 points needs one of 401,
        # of which only the 190 points needed are measured
        step_hz, segments, points = tdr.plan_sweep(50.0, 300e6, VF, 401)
        self.assertEqual((1, 190), (segments, points))
        self.assertGreaterEqual(VF * SPEED_OF_LIGHT / (2.0 * step_hz), tdr.RANGE_MARGIN * 50.0)
        self.assertLessEqual(step_hz * points, 300e6)

    def test_plan_no_extra_points(self):
        # A short cable needs no more than one scan of 101 points, whatever
        # the sweep points setting
        self.assertEqual(tdr.plan_sweep(20.0, 300e6, VF), tdr.plan_sweep(20.0, 300e6, VF, 1001))
        # Fewer configured points than the default are respected
        step_hz, segments, points = tdr.plan_sweep(20.0, 300e6, VF, 51)
        self.assertEqual(51, points)

    def test_plan_near_longest_length(self):
        # Rounding the needed points up must not push the step below the
        # smallest one when many points per scan are allowed
        needed_step_hz = 50005.0
        max_length_m = VF * SPEED_OF_LIGHT / (2.0 * tdr.RANGE_MARGIN * needed_step_hz)
        step_hz, segments, points = tdr.plan_sweep(max_length_m, 6000.1 * needed_step_hz, VF, 10001)
        self.assertGreaterEqual(step_hz, tdr.MIN_STEP_HZ)
        self.assertLessEqual(step_hz, needed_step_hz)
        self.assertLessEqual(step_hz * points * segments, 6000.1 * needed_step_hz)

    def test_plan_errors(self):
        with self.assertRaises(Exception) as context:
            tdr.plan_sweep(5000.0, 300e6, VF)
        self.assertIn("too long", str(context.exception))
        with self.assertRaises(Exception) as context:
            tdr.plan_sweep(20.0, 3e6, VF)
        self.assertIn("too low", str(context.exception))

    def test_calibration_warnings(self):
        # Inside the calibrated range, also with frequencies rounded by the device
        self.assertEqual([], tdr.calibration_warnings((50000, 300000000), 2970297, 300000000))
        self.assertEqual([], tdr.calibration_warnings((50000, 299999900), 2970297, 300000000))
        # The range isn't known
        self.assertEqual([], tdr.calibration_warnings(None, 2970297, 300000000))
        # Measured above and below the calibrated range
        warnings = tdr.calibration_warnings((50000, 30000000), 2970297, 300000000)
        self.assertEqual(1, len(warnings))
        self.assertIn("0.050 to 30.000 MHz", warnings[0])
        self.assertIn("2.970 to 300.000 MHz", warnings[0])
        self.assertEqual(1, len(tdr.calibration_warnings((10000000, 300000000), 2970297, 300000000)))

    def test_segment_ranges(self):
        self.assertEqual([(1000, 101000), (102000, 202000)], tdr.segment_ranges(1000, 2))
        self.assertEqual([(1000, 201000), (202000, 402000)], tdr.segment_ranges(1000, 2, 201))

    def test_open_cable(self):
        result = measure(lambda f: cable_rcs(f, 10.0))
        self.assertEqual(1, len(result["events"]))
        event = result["events"][0]
        self.assertAlmostEqual(10.0, float(event["distance_m"]), delta=0.05)
        self.assertAlmostEqual(1.0, float(event["reflection"]), delta=0.02)
        self.assertIn("cable end: open", event["description"])
        self.assertEqual("50", event["impedance_before"])
        self.assertEqual("more than 2000", event["impedance_after"])
        self.assertTrue(result["end"]["found"])
        self.assertEqual("open", result["end"]["state"])
        self.assertEqual(event["description"], result["end"]["description"])
        self.assertAlmostEqual(10.0, float(result["end"]["distance_m"]), delta=0.05)
        self.assertEqual([], result["warnings"])
        self.assertEqual(101, result["point_count"])
        self.assertEqual("300.000", result["end_frequency_mhz"])
        self.assertLess(float(result["resolution_m"]), 1.0)

    def test_shorted_cable(self):
        result = measure(lambda f: cable_rcs(f, 4.0, short=True))
        self.assertEqual(1, len(result["events"]))
        event = result["events"][0]
        self.assertAlmostEqual(4.0, float(event["distance_m"]), delta=0.05)
        self.assertAlmostEqual(-1.0, float(event["reflection"]), delta=0.02)
        self.assertIn("short circuit", event["description"])
        self.assertEqual("less than 1", event["impedance_after"])
        self.assertEqual("short", result["end"]["state"])

    def test_long_cable_beyond_half_range(self):
        # The phase turns by more than 180 degrees between two points
        result = measure(lambda f: cable_rcs(f, 20.0))
        self.assertEqual(1, len(result["events"]))
        self.assertAlmostEqual(20.0, float(result["end"]["distance_m"]), delta=0.05)
        self.assertEqual("open", result["end"]["state"])

    def test_lossy_cable(self):
        result = measure(lambda f: cable_rcs(f, 10.0, amplitude=0.5))
        self.assertEqual(1, len(result["events"]))
        self.assertAlmostEqual(0.5, float(result["events"][0]["reflection"]), delta=0.02)
        self.assertEqual("6.0", result["events"][0]["return_loss_db"])
        self.assertTrue(result["end"]["found"])

    def test_skin_effect_loss_open_end(self):
        # The loss spreads the reflection of a long cable's end so much that
        # its peak is lower than END_RHO; the end is still found
        result = measure(lambda f: coax_rcs(f, [(50.0, 50.0)], 1.0), max_length_m=60.0)
        self.assertEqual(1, len(result["events"]))
        event = result["events"][0]
        self.assertLess(float(event["reflection"]), tdr.END_RHO)
        self.assertIn("cable end: open", event["description"])
        self.assertGreater(float(event["impedance_after"]), 250.0)
        self.assertTrue(result["end"]["found"])
        self.assertEqual("open", result["end"]["state"])
        self.assertAlmostEqual(50.0, float(result["end"]["distance_m"]), delta=0.1)
        self.assertEqual([], result["warnings"])

    def test_skin_effect_loss_shorted_end(self):
        result = measure(lambda f: coax_rcs(f, [(50.0, 50.0)], -1.0), max_length_m=60.0)
        self.assertTrue(result["end"]["found"])
        self.assertEqual("short", result["end"]["state"])
        self.assertAlmostEqual(50.0, float(result["end"]["distance_m"]), delta=0.1)
        self.assertIn("short circuit", result["events"][0]["description"])

    def test_bump_stronger_than_lossy_end(self):
        # A 1 m piece of 100 ohm cable at 3 m reflects more strongly than the
        # lossy open end at 50 m, but it isn't taken as the cable end
        sections = [(50.0, 3.0), (100.0, 1.0), (50.0, 46.0)]
        result = measure(lambda f: coax_rcs(f, sections, 1.0), max_length_m=60.0)
        self.assertTrue(result["end"]["found"])
        self.assertAlmostEqual(50.0, float(result["end"]["distance_m"]), delta=0.1)
        bump = result["events"][0]
        self.assertAlmostEqual(3.0, float(bump["distance_m"]), delta=0.05)
        self.assertGreater(float(bump["reflection"]), float(result["events"][-1]["reflection"]))
        self.assertIn("impedance rise", bump["description"])
        self.assertAlmostEqual(100.0, float(bump["impedance_after"]), delta=10.0)
        self.assertNotIn("repeat echo", bump["description"])
        end_events = [e for e in result["events"] if "cable end: open" in e["description"]]
        self.assertEqual(1, len(end_events))
        self.assertAlmostEqual(50.0, float(end_events[0]["distance_m"]), delta=0.1)

    def test_reflection_above_one(self):
        # Points measured with an inaccurate calibration
        def rcs(f_list):
            result = cable_rcs(f_list, 10.0, amplitude=0.9)
            result[4] *= 1.4
            result[20] *= 1.2
            return result
        result = measure(rcs)
        self.assertEqual(1, len(result["warnings"]))
        self.assertIn("At 2 of the measured frequencies", result["warnings"][0])
        self.assertIn("up to 1.26 at 14.85 MHz", result["warnings"][0])
        self.assertTrue(result["end"]["found"])
        # Noise just above 1 is no reason for the warning
        result = measure(lambda f: [1.03 * rc for rc in cable_rcs(f, 10.0)])
        self.assertEqual([], result["warnings"])

    def test_impedance_step(self):
        result = measure(lambda f: two_section_rcs(f, 5.0, 75.0, 5.0))
        events = result["events"]
        self.assertGreaterEqual(len(events), 2)
        # The junction of the 50 and 75 ohm sections
        self.assertAlmostEqual(5.0, float(events[0]["distance_m"]), delta=0.05)
        self.assertAlmostEqual(0.2, float(events[0]["reflection"]), delta=0.02)
        self.assertIn("impedance rise", events[0]["description"])
        self.assertEqual("50", events[0]["impedance_before"])
        self.assertAlmostEqual(75.0, float(events[0]["impedance_after"]), delta=2.0)
        # The open end
        self.assertAlmostEqual(10.0, float(events[1]["distance_m"]), delta=0.05)
        self.assertIn("cable end: open", events[1]["description"])
        self.assertAlmostEqual(10.0, float(result["end"]["distance_m"]), delta=0.05)
        # The echo between the end and the junction
        self.assertAlmostEqual(15.0, float(events[2]["distance_m"]), delta=0.05)
        self.assertIn("repeat echo", events[2]["description"])
        self.assertIsNone(events[2]["impedance_after"])
        self.assertIn("repeat echoes", " ".join(result["warnings"]))

    def test_impedance_profile(self):
        result = measure(lambda f: two_section_rcs(f, 5.0, 75.0, 5.0))
        profile = result["profile"]
        self.assertEqual(tdr.PROFILE_ROWS + 1, len(profile))
        self.assertEqual("0.00", profile[0]["distance_m"])
        for row in profile:
            distance = float(row["distance_m"])
            if distance < 4.0:
                self.assertAlmostEqual(50.0, float(row["impedance"]), delta=2.0)
            elif 6.0 < distance < 9.0:
                self.assertAlmostEqual(75.0, float(row["impedance"]), delta=3.0)
        self.assertEqual("more than 2000", profile[-1]["impedance"])

    def test_matched_cable(self):
        result = measure(lambda f: [complex(0.001, 0.0)] * len(f))
        self.assertEqual([], result["events"])
        self.assertFalse(result["end"]["found"])
        self.assertIn("No reflections", " ".join(result["warnings"]))
        self.assertEqual("50", result["profile"][5]["impedance"])

    def test_no_cable_end(self):
        # A 75 ohm cable terminated with 75 ohms: one reflection at its start
        rho = 0.2
        result = measure(lambda f: [rho * round_trip(x, 5.0) for x in f])
        self.assertEqual(1, len(result["events"]))
        self.assertFalse(result["end"]["found"])
        self.assertIn("No cable end", " ".join(result["warnings"]))

    def test_nothing_connected(self):
        result = measure(lambda f: [complex(1.0, 0.0)] * len(f))
        self.assertEqual("0.00", result["events"][0]["distance_m"])
        self.assertIn("connection point", result["events"][0]["description"])
        self.assertIn("Is a cable connected", " ".join(result["warnings"]))

    def test_stitched_scans(self):
        result = measure(lambda f: cable_rcs(f, 30.0), max_length_m=50.0)
        self.assertEqual(202, result["point_count"])
        self.assertEqual(1, len(result["events"]))
        self.assertAlmostEqual(30.0, float(result["end"]["distance_m"]), delta=0.05)

    def test_resampled_grid(self):
        # A firmware returning four times as many points per scan
        step_hz, segments, _ = tdr.plan_sweep(20.0, 300e6, VF)
        points = tdr.SEGMENT_POINTS * segments
        fine = [step_hz + i * step_hz / 4.0 for i in range(4 * (points - 1) + 1)]
        result = tdr.analyze_tdr(fine, cable_rcs(fine, 3.0), VF, step_hz, points)
        self.assertEqual(points, result["point_count"])
        self.assertAlmostEqual(3.0, float(result["end"]["distance_m"]), delta=0.05)

    def test_bad_data(self):
        f = grid(1000000, 101)
        with self.assertRaises(Exception):
            tdr.analyze_tdr(f, [complex(1, 0)] * 100, VF, 1000000, 101)
        # Half of the requested range is missing
        with self.assertRaises(Exception):
            tdr.analyze_tdr(f[:50], [complex(1, 0)] * 50, VF, 1000000, 101)


if __name__ == '__main__':
    unittest.main()
