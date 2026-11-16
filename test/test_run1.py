import unittest
import nanovna
import math
import util 

class Run1(unittest.TestCase):

    def test_1(self):

        rc_list = [ complex(0.5, 0.5) ]
        f_list = [ 1.0 ]

        z_list = [nanovna.Nanovna.reflection_coefficient_to_z(rc) for rc in rc_list]
        self.assertEqual(50 + 100j, z_list[0]) 
        # Return loss
        s11_list = [nanovna.Nanovna.reflection_coefficient_to_s11(rc) for rc in rc_list]
        self.assertAlmostEqual(-3.098, s11_list[0], 0)
        # https://chemandy.com/calculators/return-loss-and-mismatch-calculator.htm
        vswr_list = [nanovna.Nanovna.reflection_coefficient_to_vswr(rc) for rc in rc_list]
        self.assertAlmostEqual(5.82, vswr_list[0], 1)

    def test_2(self):

        rc_list = [ complex(0.5, 0.5) ]
        f_list = [ 1.0e6 ]
        c = -nanovna.Nanovna.reflection_coefficient_to_c(rc_list[0], f_list[0])

        r = 0.999999999999
        self.assertEqual("1 F", util.fmt_si(r, "F"))
        r = 1.0
        self.assertEqual("1 F", util.fmt_si(r, "F"))
        r = 11e-3
        self.assertEqual("11 mF", util.fmt_si(r, "F"))
        r = 1.2e-6
        self.assertEqual("1.2 uF", util.fmt_si(r, "F"))
        r = 0.2299e-6
        self.assertEqual("230 nF", util.fmt_si(r, "F"))

    def test_parse_scan_lines(self):

        lines = ["7000000 1.281372 0.359714 ", "7002000 -0.5 0.25"]
        f_list, rc_list = nanovna.Nanovna.parse_scan_lines(lines)
        self.assertEqual([7000000.0, 7002000.0], f_list)
        self.assertEqual([complex(1.281372, 0.359714), complex(-0.5, 0.25)], rc_list)
        # No output (e.g. firmware whose scan has no outmask)
        with self.assertRaises(Exception):
            nanovna.Nanovna.parse_scan_lines([])
        # Frequency only
        with self.assertRaises(Exception):
            nanovna.Nanovna.parse_scan_lines(["7000000"])
        # Not a scan response
        with self.assertRaises(Exception):
            nanovna.Nanovna.parse_scan_lines(["scan? usage"])

    def test_supports_scan(self):

        class FakeNanovna(nanovna.Nanovna):
            def __init__(self, help_lines):
                self.help_lines = help_lines
            def run_command(self, command, timeout=None):
                return self.help_lines

        # NanoVNA-F style: one command per line with usage
        n = FakeNanovna(["There are all commands",
                         "scan:                usage: scan {start(Hz)} {stop(Hz)} [points] [outmask]"])
        self.assertTrue(n.supports_scan())
        # edy555 / DiSlord style: all commands on one line
        n = FakeNanovna(["Commands: help exit info echo scan sweep data frequencies"])
        self.assertTrue(n.supports_scan())
        n = FakeNanovna(["Commands: help exit info echo sweep data frequencies"])
        self.assertFalse(n.supports_scan())

    def test_scan_points_out_of_range(self):

        with self.assertRaises(nanovna.PointsOutOfRangeError):
            nanovna.Nanovna.parse_scan_lines(["sweep points exceeds range 101"])

        class FakeNanovna(nanovna.Nanovna):
            def __init__(self):
                self.scan_supported = True
                self.ser = object()
                self.commands = []
            def run_command(self, command, timeout=None):
                self.commands.append(command)
                return ["sweep points exceeds range 101"]

        # The error is reported instead of silently falling back to sweep,
        # and scan stays enabled for the next measurement
        n = FakeNanovna()
        with self.assertRaises(nanovna.PointsOutOfRangeError):
            n.measure_s11(1000000, 30000000, 401)
        self.assertEqual(["scan 1000000 30000000 401 3"], n.commands)
        self.assertTrue(n.scan_supported)

    def test_set_sweep_range(self):

        class FakeNanovna(nanovna.Nanovna):
            """ Device that accepts point counts up to max_points """
            def __init__(self, device_points, max_points, sweep_reply=[]):
                self.device_points = device_points
                self.max_points = max_points
                self.sweep_reply = sweep_reply
                self.commands = []
            def run_command(self, command, timeout=None):
                self.commands.append(command)
                if command == "frequencies":
                    return [str(i) for i in range(self.device_points)]
                tokens = command.split()
                if len(tokens) == 4 and int(tokens[3]) <= self.max_points:
                    self.device_points = int(tokens[3])
                return self.sweep_reply

        # Count already right: plain sweep only, as older firmwares expect
        n = FakeNanovna(101, 101)
        n.set_sweep_range(1000000, 30000000, 101)
        self.assertEqual(["sweep 1000000 30000000", "frequencies"], n.commands)
        # Count changed: e.g. back to 101 after a scan left the device at 301
        n = FakeNanovna(301, 301)
        n.set_sweep_range(1000000, 30000000, 101)
        self.assertEqual(["sweep 1000000 30000000", "frequencies",
                          "sweep 1000000 30000000 101", "frequencies"], n.commands)
        self.assertEqual(101, n.device_points)
        # Refused with a message, or ignored silently
        n = FakeNanovna(101, 101, ["sweep points exceeds range 101"])
        with self.assertRaisesRegex(nanovna.PointsOutOfRangeError, "exceeds range 101"):
            n.set_sweep_range(1000000, 30000000, 401)
        n = FakeNanovna(101, 101)
        with self.assertRaises(nanovna.PointsOutOfRangeError):
            n.set_sweep_range(1000000, 30000000, 401)

    def test_sweep_range(self):

        class FakeNanovna(nanovna.Nanovna):
            def __init__(self, reply):
                self.reply = reply
            def run_command(self, command, timeout=None):
                return self.reply

        self.assertEqual((50000, 300000000), FakeNanovna(["50000 300000000 301"]).sweep_range())
        self.assertEqual((50000, 300000000), FakeNanovna(["50000 300000000"]).sweep_range())
        # Answers that can't be read: no check rather than a failure
        for reply in [[], [""], ["usage: sweep {start(Hz)} [stop(Hz)] [points]"],
                      ["50000"], ["0 300000000 101"], ["300000000 50000 101"]]:
            self.assertIsNone(FakeNanovna(reply).sweep_range(), reply)

    def test_measure_s11_segments(self):

        class FakeNanovna(nanovna.Nanovna):
            def __init__(self):
                self.calls = []
            def measure_s11(self, start_frequency, end_frequency, points=nanovna.SCAN_POINTS):
                self.calls.append((start_frequency, end_frequency, points))
                return [start_frequency, end_frequency], [complex(start_frequency), complex(end_frequency)]

        n = FakeNanovna()
        frequencies, rcs = n.measure_s11_segments([(1000, 201000), (202000, 402000)], 201)
        self.assertEqual([(1000, 201000, 201), (202000, 402000, 201)], n.calls)
        self.assertEqual([1000, 201000, 202000, 402000], frequencies)
        self.assertEqual([complex(1000), complex(201000), complex(202000), complex(402000)], rcs)

    def test_validate_config_update(self):

        self.assertEqual({"port": "COM7", "points": 401},
                         nanovna.validate_config_update({"port": " COM7 ", "points": "401"}))
        self.assertEqual({"points": 11}, nanovna.validate_config_update({"points": "11"}))
        self.assertEqual({"points": 10001}, nanovna.validate_config_update({"points": "10001"}))
        for points in ["abc", "", "10", "10002", "101.5"]:
            with self.assertRaises(Exception):
                nanovna.validate_config_update({"points": points})
        with self.assertRaises(Exception):
            nanovna.validate_config_update({"port": "  "})
        with self.assertRaises(Exception):
            nanovna.validate_config_update({"workdir": "x"})

    class FakeSerial:
        """ Serial port whose reads return the given chunks, then nothing """
        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.timeout = 10
            self.written = []
            self.closed = False
        def reset_input_buffer(self):
            pass
        def reset_output_buffer(self):
            pass
        def write(self, data):
            self.written.append(data)
        def read_until(self, expected):
            return self.chunks.pop(0) if self.chunks else b""
        def read(self, size):
            return self.chunks.pop(0) if self.chunks else b""
        def close(self):
            self.closed = True

    def test_incomplete_response_closes_port(self):

        # The prompt is missing: the port is closed so it can be reopened
        ser = self.FakeSerial([b"frequencies\r\n1000000\r\n"])
        n = nanovna.Nanovna()
        n.ser = ser
        with self.assertRaisesRegex(Exception, "Timed out"):
            n.run_command("frequencies", timeout=30)
        self.assertTrue(ser.closed)
        self.assertIsNone(n.ser)
        self.assertEqual(10, ser.timeout)

    def test_flush_waits_for_late_prompts(self):

        # A busy device sends its prompts late; all of them are consumed so
        # they aren't taken as the next command's response
        ser = self.FakeSerial([b"\r\nch> ", b"\r\nch> ", b"ch> "])
        n = nanovna.Nanovna()
        n.ser = ser
        n.flush()
        self.assertEqual([], ser.chunks)
        self.assertEqual(10, ser.timeout)
        self.assertFalse(ser.closed)

if __name__ == '__main__':
    unittest.main()
