import cmath
import math
import unittest
import cable
from cable import SPEED_OF_LIGHT


def frequencies(start_hz=1e6, end_hz=30e6, points=101):
    step = (end_hz - start_hz) / (points - 1)
    return [start_hz + i * step for i in range(points)]


def cable_rcs(f_list, length_m, velocity_factor, amplitude=1.0, short=False):
    """ Reflection coefficients of an ideal cable with an open or shorted far end. """
    delay_s = length_m / (velocity_factor * SPEED_OF_LIGHT)
    sign = -1.0 if short else 1.0
    return [sign * amplitude * cmath.exp(-1j * 4 * math.pi * f * delay_s) for f in f_list]


class TestCable(unittest.TestCase):

    def test_parse_velocity_factor(self):
        self.assertEqual(0.66, cable.parse_velocity_factor("0.66"))
        self.assertEqual(1.0, cable.parse_velocity_factor(" 1 "))
        for bad in [None, "", "  ", "0", "-0.5", "1.2", "abc", "nan", "inf"]:
            with self.assertRaises(Exception):
                cable.parse_velocity_factor(bad)

    def test_open_cable(self):
        f = frequencies()
        result = cable.measure_cable(f, cable_rcs(f, 10.0, 0.66), 0.66)
        self.assertEqual("10.00", result["physical_length_m"])
        self.assertEqual("15.15", result["electrical_length_m"])
        self.assertEqual("50.54", result["delay_ns"])
        self.assertEqual("open", result["far_end"])
        self.assertEqual("0.660", result["velocity_factor"])
        self.assertEqual(101, result["point_count"])
        self.assertEqual([], result["warnings"])
        self.assertEqual(["0.00", "0.00", "0.00"], [l["loss_db"] for l in result["losses"]])
        self.assertEqual(["1.000", "15.500", "30.000"], [l["frequency_mhz"] for l in result["losses"]])

    def test_short_cable_end(self):
        f = frequencies()
        result = cable.measure_cable(f, cable_rcs(f, 25.0, 0.85, short=True), 0.85)
        self.assertEqual("25.00", result["physical_length_m"])
        self.assertEqual("short", result["far_end"])

    def test_loss(self):
        f = frequencies()
        result = cable.measure_cable(f, cable_rcs(f, 10.0, 0.66, amplitude=0.5), 0.66)
        self.assertEqual("3.01", result["losses"][1]["loss_db"])
        self.assertEqual("10.00", result["physical_length_m"])

    def test_max_length(self):
        f = frequencies()
        # Step 0.29 MHz: c / (4 * 0.29e6) = 258.4 m electrical
        result = cable.measure_cable(f, cable_rcs(f, 10.0, 1.0), 1.0)
        self.assertEqual("258.44", result["max_length_m"])
        # A cable near the limit gets a warning
        result = cable.measure_cable(f, cable_rcs(f, 230.0, 1.0), 1.0)
        self.assertEqual("230.00", result["physical_length_m"])
        self.assertIn("longer than this frequency range", " ".join(result["warnings"]))

    def test_short_cable_warning(self):
        f = frequencies()
        result = cable.measure_cable(f, cable_rcs(f, 1.0, 0.66), 0.66)
        self.assertEqual("1.00", result["physical_length_m"])
        self.assertIn("short for this frequency range", " ".join(result["warnings"]))

    def test_terminated_warning(self):
        f = frequencies()
        result = cable.measure_cable(f, cable_rcs(f, 10.0, 0.66, amplitude=0.1), 0.66)
        self.assertIn("terminated", " ".join(result["warnings"]))

    def test_reliability(self):
        f = frequencies()
        result = cable.measure_cable(f, cable_rcs(f, 10.0, 0.66), 0.66)
        self.assertEqual("good", result["reliability"])
        self.assertEqual("0.00", result["length_error_m"])
        # Short cable: fair
        result = cable.measure_cable(f, cable_rcs(f, 1.0, 0.66), 0.66)
        self.assertEqual("fair", result["reliability"])
        # Terminated or too long: low
        result = cable.measure_cable(f, cable_rcs(f, 10.0, 0.66, amplitude=0.1), 0.66)
        self.assertEqual("low", result["reliability"])
        self.assertIsNone(result["length_error_m"])
        result = cable.measure_cable(f, cable_rcs(f, 230.0, 1.0), 1.0)
        self.assertEqual("low", result["reliability"])
        self.assertIsNone(result["length_error_m"])

    def test_noisy_fit_is_fair_with_warning(self):
        f = frequencies()
        clean = cable_rcs(f, 10.0, 0.66)
        noisy = [rc * cmath.exp(1j * math.radians(10.0 if i % 2 else -10.0)) for i, rc in enumerate(clean)]
        result = cable.measure_cable(f, noisy, 0.66)
        self.assertEqual("fair", result["reliability"])
        self.assertIn("straight line", " ".join(result["warnings"]))
        self.assertGreater(float(result["length_error_m"]), 0.0)

    def test_length_error_grows_with_noise(self):
        f = frequencies()
        clean = cable_rcs(f, 10.0, 0.66)
        noisy = [rc * cmath.exp(1j * math.radians(3.0 if i % 2 else -3.0)) for i, rc in enumerate(clean)]
        result = cable.measure_cable(f, noisy, 0.66)
        self.assertGreater(float(result["length_error_m"]), 0.0)
        self.assertEqual("good", result["reliability"])

    def test_no_rotation(self):
        f = frequencies()
        with self.assertRaises(Exception):
            cable.measure_cable(f, [complex(1, 0)] * len(f), 0.66)
        # Phase increasing with frequency is not a cable
        with self.assertRaises(Exception):
            cable.measure_cable(f, cable_rcs(f, -10.0, 0.66), 0.66)

    def test_far_end_state(self):
        self.assertEqual("open", cable.far_end_state(10))
        self.assertEqual("open", cable.far_end_state(350))
        self.assertEqual("short", cable.far_end_state(-170))
        self.assertTrue(cable.far_end_state(90).startswith("unclear"))


if __name__ == '__main__':
    unittest.main()
