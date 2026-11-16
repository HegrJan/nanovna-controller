import serial
import logging
import glob
import sys
import math
import time

# Time for the NanoVNA to complete a fresh sweep after the range is changed
SWEEP_SETTLE_TIME_S = 4
# "scan" answers only once all points are measured, which can take several
# seconds (more with many points or low frequencies)
SCAN_TIMEOUT_S = 30
# Usual response timeout; the device can be busy finishing a sweep for a few
# seconds before it answers (more with many points)
RESPONSE_TIMEOUT_S = 10
# flush() takes the device as done answering once nothing more arrives for
# this long after its prompt
FLUSH_QUIET_S = 0.2
# Default number of measured points (supported by all devices) and the range
# accepted in the configuration. The actual maximum depends on the device
# (e.g. 101 on the original NanoVNA, 401 on the NanoVNA-H4).
SCAN_POINTS = 101
MIN_SCAN_POINTS = 11
MAX_SCAN_POINTS = 10001
# scan outmask bits: 1 = frequency, 2 = channel 0 (S11)
SCAN_OUTMASK_FREQ_S11 = 3


class PointsOutOfRangeError(Exception):
    """ The device refused the requested number of scan points. """
    pass


def validate_config_update(form):
    """
    Checks the configuration values posted from the Administration panel.

    :param form: A dict of the posted values (strings)
    :return: A dict of the validated values to store
    :raises Exception: With a user readable message if a value is invalid
    """
    result = {}
    for key, value in form.items():
        if key == "port":
            port = value.strip()
            if port == "":
                raise Exception("Serial port is missing")
            result["port"] = port
        elif key == "points":
            try:
                points = int(value.strip())
            except ValueError:
                raise Exception("Invalid sweep points: " + value)
            if points < MIN_SCAN_POINTS or points > MAX_SCAN_POINTS:
                raise Exception("Sweep points must be between {} and {}".format(
                    MIN_SCAN_POINTS, MAX_SCAN_POINTS))
            result["points"] = points
        else:
            raise Exception("Unknown setting: " + key)
    return result


class Nanovna:
    """
    Interface for communicating with a NanoVNA
    """
    ser = None
    port = None
    # Whether the firmware has a usable "scan" command; None = not checked yet
    # on this connection
    scan_supported = None

    def connect_if_necessary(self, port):
        """ Opens a connection to the NanoVNA if necessary. """
        # Look for the case where the port is changing
        if port != self.port:
            self.port = None
            self.disconnect()
        # Look for the case were we need to open the port
        if self.ser is None or not self.ser.isOpen():
            logging.info("Connection was not open yet: " + port)
            try:
                # Open the serial port to the NanoVNA
                self.ser = serial.Serial(port, timeout=RESPONSE_TIMEOUT_S, write_timeout=2)
                self.port = port
                self.scan_supported = None
                self.flush()
                logging.info("Connection is good")
            except Exception as ex:
                logging.error("Unable to open connection", exc_info=True)
                self.disconnect()

            if self.ser is None:
                raise Exception("Unable to connect to NanoVNA")
        else:
            pass

    def disconnect(self):
        """
        Closes the connection, e.g. after an error left the responses out of
        step. The port is closed explicitly, since Windows refuses to open a
        COM port again while an old handle to it is still open.
        """
        ser = self.ser
        self.ser = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                logging.warning("Unable to close the serial port", exc_info=True)

    def flush(self):
        """
        Discards anything left in the serial buffers (half-typed commands, a stale
        prompt, output of an earlier command) so the next response lines up with
        the next command. Similar to NanoVNA-Saver's flushSerialBuffers(), but it
        waits for the prompts: a device busy finishing a sweep answers only after
        a few seconds, and a late prompt would be taken as the response to the
        next command.
        """
        ser = self.ser
        default_timeout = ser.timeout
        try:
            ser.reset_input_buffer()
            ser.write(b"\r\n\r\n")
            ser.timeout = RESPONSE_TIMEOUT_S
            if not ser.read_until(b"ch> ").endswith(b"ch> "):
                logging.warning("No prompt from NanoVNA while flushing")
            # Discard the remaining prompts until the device is quiet
            ser.timeout = FLUSH_QUIET_S
            deadline = time.monotonic() + RESPONSE_TIMEOUT_S
            while len(ser.read(1024)) > 0 and time.monotonic() < deadline:
                pass
        finally:
            ser.timeout = default_timeout
        ser.reset_input_buffer()
        ser.reset_output_buffer()

    def read_response_as_lines(self):
        """
        :param ser: The serial port that is connected (an open) to the NanoVNA
        :return: An array of strings, one for each line returned
        """
        if self.ser is None:
            raise Exception("Not connected")
        # The ch> prompt is used as the delimiter
        try:
            data = self.ser.read_until(b'ch> ')
        except Exception:
            self.disconnect()
            raise
        if len(data) == 0:
            raise Exception("No data received from NanoVNA")
        # Without the prompt the response is incomplete (timeout); the rest
        # would arrive later and be taken as the next command's response
        if not data.endswith(b'ch> '):
            raise Exception("Timed out waiting for the NanoVNA response")
        # Pull off the last part (not relevant data)
        data = data[:-4]
        # Break into lines and then decode into strings.
        return [line.decode("utf-8") for line in data.splitlines()]

    def run_command(self, command, timeout=None):
        """
        :param command: The command to send
        :param timeout: Read timeout in seconds for this command only (default:
            the port's timeout)
        :return: An array of strings, one for each line returned by the command
        """
        if self.ser is None:
            raise Exception("Not connected")
        logging.info("NanoVNA command: " + command)
        ser = self.ser
        default_timeout = ser.timeout
        try:
            if timeout is not None:
                ser.timeout = timeout
            # Drop unsolicited leftovers so they aren't taken as this response
            ser.reset_input_buffer()
            ser.write((command + "\r").encode("utf-8"))
            lines = self.read_response_as_lines()
        except Exception:
            # The rest of the response may still arrive, so start over with a
            # fresh connection (flushed when it is opened)
            self.disconnect()
            raise
        finally:
            ser.timeout = default_timeout
        # We discard the first line because that is the echo of the command
        return lines[1:]

    def get_complex_data(self):
        lines = self.run_command("data")
        # Parse into real/imaginary tokens
        tokenized_lines = [line.split(" ") for line in lines]
        # Make into imaginary numbers
        return [complex(float(token[0]), float(token[1])) for token in tokenized_lines]

    def supports_scan(self):
        """
        Checks (once per connection) whether "scan" is among the commands listed
        by "help". Firmwares format the list differently (one per line with a
        usage text, or all on one line), so any whitespace separated word counts.
        """
        if self.scan_supported is None:
            words = " ".join(self.run_command("help")).split()
            self.scan_supported = any(w.rstrip(":,") == "scan" for w in words)
            logging.info("Firmware supports scan: " + str(self.scan_supported))
        return self.scan_supported

    @staticmethod
    def parse_scan_lines(lines):
        """
        :param lines: Output of "scan" with the frequency and S11 outmask, one
            "frequency real imaginary" line per point
        :return: A list of frequencies and a list of complex reflection coefficients
        """
        frequency_list = []
        rc_list = []
        for line in lines:
            # E.g. "sweep points exceeds range 101" when more points are
            # requested than the firmware supports
            if "exceeds range" in line:
                raise PointsOutOfRangeError(
                    "The device doesn't support this many points ({}). "
                    "Lower Sweep Points on the Administration panel.".format(line.strip()))
            tokens = line.split()
            if len(tokens) < 3:
                raise Exception("Unexpected scan output: " + line)
            frequency_list.append(float(tokens[0]))
            rc_list.append(complex(float(tokens[1]), float(tokens[2])))
        if len(frequency_list) == 0:
            raise Exception("No data returned by scan")
        return frequency_list, rc_list

    def scan_s11(self, start_frequency, end_frequency, points=SCAN_POINTS):
        """
        Measures S11 with "scan", which returns only after a fresh measurement of
        the requested range (with the active calibration applied).
        """
        # More points take proportionally longer to measure
        timeout = max(SCAN_TIMEOUT_S, SCAN_TIMEOUT_S * points / SCAN_POINTS)
        lines = self.run_command("scan {} {} {} {}".format(
            int(start_frequency), int(end_frequency), int(points), SCAN_OUTMASK_FREQ_S11),
            timeout=timeout)
        return self.parse_scan_lines(lines)

    def sweep_point_count(self):
        """ :return: The number of points the device currently sweeps """
        return len(self.run_command("frequencies", timeout=SCAN_TIMEOUT_S))

    def set_sweep_range(self, start_frequency, end_frequency, points=SCAN_POINTS):
        """
        Sets the device's sweep range and number of points, e.g. before
        calibrating. The range is set without the number of points first,
        since older firmwares don't accept it; the number of points is sent
        only when the device uses a different one (some firmwares also change
        it with "scan").
        """
        self.run_command("sweep {} {}".format(int(start_frequency), int(end_frequency)))
        if self.sweep_point_count() == points:
            return
        # Changing the count makes the device busy for a few seconds, which
        # can delay the responses beyond the port's default timeout
        lines = self.run_command("sweep {} {} {}".format(
            int(start_frequency), int(end_frequency), int(points)), timeout=SCAN_TIMEOUT_S)
        # Firmwares report a refused count differently ("exceeds range",
        # a usage text) or ignore it silently, so check what the device uses
        if self.sweep_point_count() != points:
            detail = " ".join(line.strip() for line in lines).strip()
            raise PointsOutOfRangeError(
                "The device didn't accept {} sweep points{}. "
                "Lower Sweep Points on the Administration panel.".format(
                    int(points), " (" + detail + ")" if detail else ""))

    def sweep_s11(self, start_frequency, end_frequency):
        """
        Measures S11 by setting the sweep range and reading back the data, for
        firmwares without a usable "scan".
        """
        self.run_command("sweep {} {}".format(int(start_frequency), int(end_frequency)))
        # "sweep" returns immediately while the device keeps sweeping in the
        # background; "data" would return the previous range/calibration until
        # a new sweep has completed
        time.sleep(SWEEP_SETTLE_TIME_S)
        lines = self.run_command("frequencies")
        frequency_list = [float(line) for line in lines]
        rc_list = self.get_complex_data()
        if len(frequency_list) != len(rc_list):
            raise Exception("Data length error")
        return frequency_list, rc_list

    def measure_s11(self, start_frequency, end_frequency, points=SCAN_POINTS):
        """
        :param points: Number of points to measure with "scan"; the sweep
            fallback uses the device's own number of points
        :return: A list of frequencies and a list of complex reflection
            coefficients for the range, using "scan" when the firmware supports
            it and the sweep/readout otherwise
        """
        if self.supports_scan():
            try:
                result = self.scan_s11(start_frequency, end_frequency, points)
                logging.info("Measured with scan")
                return result
            except PointsOutOfRangeError:
                # scan works, only the point count is too high for this device
                raise
            except Exception:
                # A dropped connection isn't a firmware limitation
                if self.ser is None:
                    raise
                # E.g. an older firmware whose scan has no outmask and prints nothing
                logging.warning("scan output unusable, falling back to sweep", exc_info=True)
                self.scan_supported = False
                self.flush()
        result = self.sweep_s11(start_frequency, end_frequency)
        logging.info("Measured with sweep")
        return result

    @staticmethod
    def reflection_coefficient_to_vswr(rc):
        """
        :param rc: A complex reflection coefficient
        :return: The scalar VSWR
        """
        gamma = abs(rc)
        return (1.0 + gamma) / (1.0 - gamma)


    @staticmethod
    def reflection_coefficient_to_z(rc):
        """
        Z = Zo * ((1 + Γ) / (1 - Γ))
        """
        return 50.0 * (1.0 + rc) / (1 - rc)

    @staticmethod
    def reflection_coefficient_to_s11(rc):
        """
        s11 = 20 * log10(mag(Γ))
        """
        gamma = abs(rc)
        return 20.0 * math.log10(gamma)

    @staticmethod
    def reflection_coefficient_to_c(rc, f):
        """
        Z = Zo * ((1 + Γ) / (1 - Γ))
        C = -1 / (w * Zi)
        """
        z = 50.0 * (1.0 + rc) / (1 - rc)
        w = 2 * math.pi * f
        return -1 / (w * z.imag)

    @staticmethod
    def reflection_coefficient_to_l(rc, f):
        """
        Z = Zo * ((1 + Γ) / (1 - Γ))
        L = Zl / w
        """
        z = 50.0 * (1.0 + rc) / (1 - rc)
        w = 2 * math.pi * f
        return z.imag / w



    @staticmethod
    def list_serial_ports():
        """ Lists serial port names

            :raises EnvironmentError:
                On unsupported or unknown platforms
            :returns:
                A list of the serial ports available on the system
        """
        if sys.platform.startswith('win'):
            ports = ['COM%s' % (i + 1) for i in range(256)]
        elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
            # this excludes your current terminal "/dev/tty"
            ports = glob.glob('/dev/tty[A-Za-z]*')
        elif sys.platform.startswith('darwin'):
            ports = glob.glob('/dev/tty.*')
        else:
            raise EnvironmentError('Unsupported platform')

        # This is somewhat crude, but it's the only way I know to check for the 
        # existence of a COMx port on Windows.
        result = []
        for port in ports:
            try:
                s = serial.Serial(port)
                s.close()
                result.append(port)
            except (OSError, serial.SerialException):
                pass

        return result
