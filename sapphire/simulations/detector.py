"""Common HiSPARC station response simulations

These are some common simulations for HiSPARC detectors.

"""
from math import sqrt, acos, pi, cos, sin

import numpy as np

from .base import BaseSimulation
from ..utils import ceil_in_base


class HiSPARCSimulation(BaseSimulation):

    def __init__(self, *args, **kwargs):
        super(HiSPARCSimulation, self).__init__(*args, **kwargs)

        for station in self.cluster.stations:
            station.gps_offset = self.simulate_station_offset()
            for detector in station.detectors:
                detector.offset = self.simulate_detector_offset()

        # Store updated version of the cluster
        self.coincidence_group._v_attrs.cluster = self.cluster

    @classmethod
    def simulate_detector_offsets(cls, n_detectors):
        """Get multiple detector offsets

        :param n_detectors: number of offsets to return.

        """
        return [cls.simulate_detector_offset() for _ in range(n_detectors)]

    @classmethod
    def simulate_detector_offset(cls):
        """Simulate time offsets between detectors in one station

        This offset should be fixed for each detector for a simulation run.

        """
        return np.random.normal(0, 2.77)

    @classmethod
    def simulate_station_offset(cls):
        """Simulate time offsets between different stations

        This offset should be fixed for each station for a simulation run.
        The actual distribution is not yet very clear. We assume it is
        gaussian for convenience. Then the stddev is about 16 ns.

        """
        return np.random.normal(0, 16)

    @classmethod
    def simulate_gps_uncertainty(cls):
        """Simulate uncertainty from GPS receiver"""

        return np.random.normal(0, 4.5)

    @classmethod
    def simulate_adc_sampling(cls, t):
        """Simulate ADC time binning due to the sampling frequency

        :param t: time to be binned.
        :returns: time ceiled in 2.5 ns base.

        """
        return ceil_in_base(t, 2.5)

    @classmethod
    def simulate_signal_transport_time(cls, n=1):
        """ Simulate transport times of scintillation light to the PMT

        Generates random transit times within a given distribution and
        adds it to the times the particles passed the detector.

        Be careful when editting this function, be sure to check both
        the single and vectorized part.

        :param n: number of times to simulate
        :returns: list of signal transport times

        """
        numbers = np.random.random(n)
        if n < 20:
            dt = np.array([2.5507 + 2.39885 * number if number < 0.39377 else
                           1.56764 + 4.89536 * number for number in numbers])
        else:
            dt = np.where(numbers < 0.39377,
                          2.5507 + 2.39885 * numbers,
                          1.56764 + 4.89536 * numbers)
        return dt

    @classmethod
    def simulate_detector_mips(cls, n, theta):
        """Simulate the detector signal for particles

        Simulation of convoluted distribution of electron and
        muon energy losses with the scintillator response

        Be careful when editting this function, be sure to check both
        the single and vectorized part.

        :param n: number of particles.
        :param theta: angle of incidence of the particles, as float or array.

        """
        costheta = np.cos(theta)
        y = np.random.random(n)

        if n == 1:
            if y < 0.3394:
                mips = (0.48 + 0.8583 * sqrt(y)) / costheta
            elif y < 0.4344:
                mips = (0.73 + 0.7366 * y) / costheta
            elif y < 0.9041:
                mips = (1.7752 - 1.0336 * sqrt(0.9267 - y)) / costheta
            else:
                mips = (2.28 - 2.1316 * sqrt(1 - y)) / costheta
        else:
            mips = np.where(y < 0.3394,
                            (0.48 + 0.8583 * np.sqrt(y)) / costheta,
                            (0.73 + 0.7366 * y) / costheta)
            mips = np.where(y < 0.4344, mips,
                            (1.7752 - 1.0336 * np.sqrt(0.9267 - y)) / costheta)
            mips = np.where(y < 0.9041, mips,
                            (2.28 - 2.1316 * np.sqrt(1 - y)) / costheta)
            mips = sum(mips)
        return mips

    @classmethod
    def generate_core_position(cls, R):
        """Generate a random core position within a circle

        DF: This is the fastest implementation I could come up with.  I
        timed several permutations of numpy / math, and tried a Monte
        Carlo method in which I pick x and y in a square (fast) and then
        determine if they fall inside a circle or not (surprisingly
        slow, because of an if-statement, and despite some optimizations
        suggested by HM).

        :param R: Maximum core distance, in meters.
        :returns: Random x, y position in the disc with radius R.

        """
        r = sqrt(np.random.uniform(0, R ** 2))
        phi = np.random.uniform(-pi, pi)
        x = r * cos(phi)
        y = r * sin(phi)
        return x, y

    @classmethod
    def generate_zenith(cls):
        """Generate a random zenith

        Pick from the expected zenith distribution.

        TODO: There is a difference between expected shower zeniths
        detected on the ground and at the top of the atmosphere. So
        simulations that use CORSIKA generated showers need to take that
        into account.

        """
        p = np.random.random()
        return cls.inverse_zenith_probability(p)

    @classmethod
    def inverse_zenith_probability(cls, p):
        """Inverse cumulative probability distribution for zenith

        Derrived from Schultheiss "The acceptancy of the HiSPARC Network",
        (internal note), eq 2.4 from Rossi.

        :param p: probability value between 0 and 1.
        :returns: zenith with corresponding cumulative probability.

        """
        return acos((1 - p) ** (1 / 8.))

    @classmethod
    def generate_azimuth(cls):
        """Generate a random azimuth

        Showers from each azimuth have equal probability

        """
        return np.random.uniform(-pi, pi)


class ErrorlessSimulation(HiSPARCSimulation):

    @classmethod
    def simulate_detector_offsets(cls, n_detectors):

        return [0.] * n_detectors

    @classmethod
    def simulate_detector_offset(cls):

        return 0.

    @classmethod
    def simulate_station_offset(cls):

        return 0.

    @classmethod
    def simulate_gps_uncertainty(cls):

        return 0.

    @classmethod
    def simulate_adc_sampling(cls, t):

        return t

    @classmethod
    def simulate_signal_transport_time(cls, n=1):

        return np.array([0.] * n)

    @classmethod
    def simulate_detector_mips(cls, n, theta):

        return n
