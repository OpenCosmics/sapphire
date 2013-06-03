import zlib
from itertools import izip

import tables
import numpy as np
from scipy.stats import norm
from scipy.optimize import curve_fit, leastsq
import progressbar as pb

from sapphire.storage import ProcessedHisparcEvent


ADC_THRESHOLD = 20
ADC_TIME_PER_SAMPLE = 2.5e-9


class ProcessEvents(object):

    """Process HiSPARC events to obtain several observables.

    This class can be used to process a set of HiSPARC events and adds a
    few observables like particle arrival time and number of particles in
    the detector to a copy of the event table.
    """

    def __init__(self, data, group, source=None):
        """Initialize the class.

        :param data: the PyTables datafile.
        :param group: the group containing the station data.  In normal
            cases, this is simply the group containing the events table.
        :param source: the name of the events table.  Default: None,
            meaning the default name 'events'.

        """
        self.data = data
        self.group = data.getNode(group)
        self.source = self._get_source(source)
        self.limit = None

    def process_and_store_results(self, destination=None, overwrite=False,
                                  limit=None):
        """Process events and store the results.

        :param destination: name of the table where the results will be
            written.  The default, None, corresponds to 'events'.
        :param overwrite: if True, overwrite previously obtained results.
        :param limit: the maximum number of events that will be stored.
            The default, None, corresponds to no limit.

        """
        self.limit = limit

        self._check_destination(destination, overwrite)

        self._create_results_table()
        self._store_results_from_traces()
        self._store_number_of_particles()
        self._move_results_table_into_destination()

    def get_traces_for_event(self, event):
        """Return the traces from an event.

        :param event: a row from the events table.
        :returns: the traces: an array of pulseheight values.

        """
        traces = []
        for idx in event['traces']:
            if not idx < 0:
                traces.append(self._get_trace(idx))

        # Make traces follow NumPy conventions
        traces = np.array(traces).T
        return traces

    def get_traces_for_event_index(self, idx):
        """Return the traces from event #idx.

        :param idx: the index number of the event.
        :returns: the traces: an array of pulseheight values.

        """
        event = self.source[idx]
        return self.get_traces_for_event(event)

    def _get_source(self, source):
        """Return the table containing the events.

        :param source: the *name* of the table.  If None, this method will
            try to find the original events table, even if the events were
            previously processed.
        :returns: table object.

        """
        if source is None:
            if '_events' in self.group:
                source = self.group._events
            else:
                source = self.group.events
        else:
            source = self.data.getNode(self.group, source)
        return source

    def _check_destination(self, destination, overwrite):
        """Check if the destination is valid"""

        if destination == '_events':
            raise RuntimeError("The _events table is reserved for internal use.  Choose another destination.")
        elif destination is None:
            destination = 'events'

        # If destination == source, source will be moved out of the way.  Don't
        # worry.  Otherwise, destination may not exist or will be overwritten
        if self.source.name != destination:
            if destination in self.group and not overwrite:
                raise RuntimeError("I will not overwrite previous results (unless you specify overwrite=True)")

        self.destination = destination

    def _create_results_table(self):
        """Create results table containing the events."""

        self._tmp_events = self._create_empty_results_table()
        self._copy_events_into_table()

    def _create_empty_results_table(self):
        """Create empty results table with correct length."""

        if self.limit:
            length = self.limit
        else:
            length = len(self.source)

        if '_t_events' in self.group:
            self.data.removeNode(self.group, '_t_events')
        table = self.data.createTable(self.group, '_t_events',
                                      ProcessedHisparcEvent,
                                      expectedrows=length)

        for x in xrange(length):
            table.row.append()
        table.flush()

        return table

    def _copy_events_into_table(self):
        table = self._tmp_events
        source = self.source

        progressbar = pb.ProgressBar(widgets=[pb.Percentage(), pb.Bar(), pb.ETA()])

        for col in progressbar(source.colnames):
            getattr(table.cols, col)[:self.limit] = getattr(source.cols,
                                                            col)[:self.limit]
        table.flush()

    def _store_results_from_traces(self):
        table = self._tmp_events

        timings = self.process_traces()

        # Assign values to full table, column-wise.
        for idx in range(4):
            col = 't%d' % (idx + 1)
            getattr(table.cols, col)[:] = timings[:, idx]
        table.flush()

    def process_traces(self, limit=None):
        """Process traces to yield pulse timing information.

        :param limit: number of rows to process.
        :returns: arrival times in each detector for the given table.

        """
        if limit:
            self.limit = limit

        events = self.source.iterrows(stop=self.limit)
        timings = self._process_traces_from_event_list(events,
                                                       length=self.limit)
        return timings

    def _process_traces_from_event_list(self, events, length=None):
        """Process traces from a list of events.

        This is the method looping over all events.

        :param events: an iterable of the events.
        :param length: an indication of the number of events, for use as a
            progress bar.  Optional.

        """
        progressbar = self._create_progressbar_from_iterable(events, length)

        result = []
        for event in progressbar(events):
            timings = self._reconstruct_time_from_traces(event)
            result.append(timings)
        result = np.array(result)

        # Replace NaN with -999, get timings in ns
        timings = np.where(np.isnan(result), -999, 1e9 * result)
        return timings

    def _create_progressbar_from_iterable(self, iterable, length=None):
        """Create a progressbar object from any iterable."""

        if length is None:
            try:
                length = len(iterable)
            except TypeError:
                pass

        if length:
            return pb.ProgressBar(maxval=length, widgets=[pb.Percentage(),
                                                          pb.Bar(), pb.ETA()])
        else:
            # Cannot create progressbar, return no-op
            return lambda x: x

    def _reconstruct_time_from_traces(self, event):
        """Reconstruct arrival times for a single event.

        This method loops over the traces.

        :param event: row from the events table.

        """
        timings = []
        for baseline, pulseheight, trace_idx in zip(event['baseline'],
                                                    event['pulseheights'],
                                                    event['traces']):
            if pulseheight < ADC_THRESHOLD:
                timings.append(np.nan)
            else:
                trace = self._get_trace(trace_idx)
                timings.append(self._reconstruct_time_from_trace(trace,
                                                                 baseline))
        return timings

    def _get_trace(self, idx):
        """Returns a trace given an index into the blobs array.

        Decompress a trace from the blobs array.

        :param idx: index into the blobs array
        :returns: array of pulseheight values

        """
        blobs = self.group.blobs

        trace = zlib.decompress(blobs[idx]).split(',')
        if trace[-1] == '':
            del trace[-1]
        trace = np.array([int(x) for x in trace])
        return trace

    def _reconstruct_time_from_trace(self, trace, baseline):
        """Reconstruct time of measurement from a trace.

        This method is doing the hard work.

        :param trace: array containing pulseheight values.
        :param baseline: baseline of the trace
        :returns: arrival time

        """
        threshold = baseline + ADC_THRESHOLD

        value = np.nan
        for i, t in enumerate(trace):
            if t >= threshold:
                value = i
                break

        return value * ADC_TIME_PER_SAMPLE

    def _store_number_of_particles(self):
        """Store number of particles in the detectors.

        Process all pulseheights from the events and estimate the number
        of particles in each detector.

        """
        table = self._tmp_events

        n_particles = self._process_pulseheights()
        for idx in range(4):
            col = 'n%d' % (idx + 1)
            getattr(table.cols, col)[:] = n_particles[:, idx]
        table.flush()
    
      
    def _process_pulseheights(self, limit=None):
        """Process pulseheights to particle density

        :returns: array of number of particles per detector

        """
        if limit:
            self.limit = limit

        pulseheights = self.source.col('pulseheights')[:self.limit]
        
        bins = np.arange(0, 5000, 10)
        mpv = self._pulse_gauss_fit(pulseheights, bins)
        n_particles = pulseheights / mpv 

        return n_particles

    def _process_pulseintegrals(self, limit=None):
        """Process pulseintegrals to particle density

        :returns: array of number of particles per detector

        """
        if limit:
            self.limit = limit

        pulseintegrals = self.source.col('integrals')[:self.limit]
        
        bins = np.arange(0, 50000, 100)
        mpv = self._pulse_gauss_fit(pulseintegrals, bins)
        n_particles = pulseintegrals / mpv 

        return n_particles
    
    def _pulse_gauss_fit(self, pulsecounts, bins):
        """Make Gauss fit to MIP peak to find MPV

        :param pulseheights: array of pulseheights for each detector.
        :returns: list with mpv values for each detector.

        """
        mpv = []
        data = pulsecounts
        
        
        for i in range(len(pulsecounts[0])):
            # Make histogram: occurence of dPulseheight vs pulseheight
            # Number of bins is important

            occurence, bins = np.histogram(pulsecounts[:, i], bins=bins)
            pulsecount = (bins[:-1] + bins[1:]) / 2

            # Get fit parameters
            average_pulsecount = (pulsecount * occurence).sum() / occurence.sum()

            if average_pulsecount < 100:
                raise ValueError( "Average pulseintegral is less than 100" )

            peak, minRange, maxRange = self.getFitParameters(pulsecount, occurence)
            width = peak - minRange
            peakOrig = peak

            # Check the width. More than 40 ADC is nice, just to be able to have a fit
            # at all.

            if width <= 40:
                fitParameters = np.zeros(3)
                fitCovariance = np.zeros((3,3))

                fitResult = [fitParameters, fitCovariance]
                chiSquare = -1

                return width, fitResult, chiSquare

            # Cut our data set such that it only include minRange < pulseintegral < maxRange

            fit_window_pulsecount = []
            fit_window_occurence = []
            for i in range(len(pulsecount)):
                if pulsecount[i] < minRange:
                    continue

                if pulsecount[i] > maxRange:
                    continue

                fit_window_pulsecount.append(pulsecount[i])
                fit_window_occurence.append(occurence[i])

            # Initial parameter values

            initial_N = 16
            initial_mean = peak
            initial_width = width

            # Fit

            fitResult = leastsq(self._residual,
                                [initial_N, initial_mean, initial_width],
                                args=(fit_window_pulsecount,
                                      fit_window_occurence),
                                full_output=1)

            fitParameters = fitResult[0]
            fitCovariance = fitResult[1]

            # Calculate the Chi2

            chiSquare = sum(self._residual(fitParameters, fit_window_pulsecount, fit_window_occurence))
            mpv.append(fitParameters[1])

        return mpv

    
    def _residual(self, params, x, data):
        """Residual which is to be minimized"""
        # Fit function
        gauss = lambda x, N, m, s: N * norm.pdf(x, m, s)
        constant = params[0]
        mean = params[1]
        width = params[2]

        model = gauss(x, constant, mean, width)

        return (data - model)

    def findBinNextMinimum(self, y, startBin):

        minY = y[startBin]

        for i in range(startBin, len(y)+1):
            currentY = y[i]

            if currentY < minY:
                minY = y[i]
            elif currentY > minY:
                return i - 1

    def findBinNextMaximum(self, y, startBin):

        maxY = y[startBin]

        for i in range(startBin, len(y) + 1):
            currentY = y[i]

            if currentY > maxY:
                maxY = y[i]
            elif currentY < maxY:
                return i - 1
    
    def smooth_forward(self, y, n=5):
        
        y_smoothed = []
        
        for i in range(0, len(y)-n):
            sum = np.sum(y[i:i+n])
            avg = sum / n
            y_smoothed.append(avg)

        return y_smoothed

    def getFitParameters(self, x, y):

        bias = (x[1]-x[0])*2

        # Rebin x

        x_rebinned = x.tolist()
        if len(x_rebinned) % 2 == 1:
            x_rebinned.append(x_rebinned[-1] + x_rebinned[1] - x_rebinned[0])
        x_rebinned = np.float_(x_rebinned)
        x_rebinned = x_rebinned.reshape(len(x_rebinned)/2, 2).mean(axis=1)

        # Smooth y by averaging while keeping sharp cut at 120 ADC

        y_smoothed = self.smooth_forward(y, 5)

        for i in range(len(y_smoothed)):
            if x[i] > 120:
                break
        y_smoothed[i] = 0

        y_smoothed = np.float_(y_smoothed)

        # First derivative y while keeping sharp cut at 120 ADC

        if len(y_smoothed) % 2 == 1:
            y_smoothed = y_smoothed.tolist()
            y_smoothed.append(0.0)
            y_smoothed = np.float_(y_smoothed)

        y_smoothed_rebinned = 2 * y_smoothed.reshape(len(y_smoothed) / 2, 2).mean(axis=1)

        y_diff = np.diff(y_smoothed_rebinned)

        for i in range(len(y_diff)):
            if x_rebinned[i] > 120:
                break

            y_diff[i] = 0

        # Smooth y by averaging

        y_diff_smoothed = np.convolve(y_diff, [0.2, 0.2, 0.2, 0.2, 0.2], "same")

        # Find approx max using the derivative

        binMinimum = self.findBinNextMinimum(y_diff_smoothed, 0)
        binMaximum = self.findBinNextMaximum(y_diff_smoothed, binMinimum)
        binMinimum = self.findBinNextMinimum(y_diff_smoothed, binMaximum)

        maxX = x_rebinned[binMaximum]
        minX = x_rebinned[binMinimum]

        # Return fit peak, fit range minimum = maxX, fit range maximum = minX

        return (maxX + minX) / 2 + bias, maxX + bias, minX + bias
  
    def _move_results_table_into_destination(self):
        if self.source.name == 'events':
            self.source.rename('_events')
            self.source = self.group._events

        if self.destination in self.group:
            self.data.removeNode(self.group, self.destination)
        self._tmp_events.rename(self.destination)

    def determine_detector_timing_offsets(self, timings_table='events'):
        """Determine the offsets between the station detectors."""

        table = self.data.getNode(self.group, timings_table)
        t2 = table.col('t2')

        gauss = lambda x, N, m, s: N * norm.pdf(x, m, s)
        bins = np.arange(-100 + 1.25, 100, 2.5)

        print "Determining offsets based on # events:",
        offsets = []
        for timings in 't1', 't3', 't4':
            timings = table.col(timings)
            dt = (timings - t2).compress((t2 >= 0) & (timings >= 0))
            print len(dt),
            y, bins = np.histogram(dt, bins=bins)
            x = (bins[:-1] + bins[1:]) / 2
            popt, pcov = curve_fit(gauss, x, y, p0=(len(dt), 0., 10.))
            offsets.append(popt[1])
        print

        return [offsets[0]] + [0.] + offsets[1:]


class ProcessIndexedEvents(ProcessEvents):

    """Process a subset of events using an index.

    This is a subclass of :class:`ProcessEvents`.  Using an index, this
    class will only process a subset of events, thus saving time.  For
    example, this class can only process events making up a coincidence.
    """

    def __init__(self, data, group, indexes, source=None):
        """Initialize the class.

        :param data: the PyTables datafile
        :param group: the group containing the station data.  In normal
            cases, this is simply the group containing the events table.
        :param indexes: a list of indexes into the events table.
        :param source: the name of the events table.  Default: None,
            meaning the default name 'events'.

        """
        super(ProcessIndexedEvents, self).__init__(data, group, source)
        self.indexes = indexes

    def _store_results_from_traces(self):
        table = self._tmp_events

        timings = self.process_traces()

        for event, (t1, t2, t3, t4) in izip(table.itersequence(self.indexes),
                                            timings):
            event['t1'] = t1
            event['t2'] = t2
            event['t3'] = t3
            event['t4'] = t4
            event.update()

        table.flush()

    def process_traces(self):
        """Process traces to yield pulse timing information.

        This method makes use of the indexes to build a list of events.

        """
        events = self.source.itersequence(self.indexes)

        timings = self._process_traces_from_event_list(events,
                                                       length=len(self.indexes))
        return timings

    def get_traces_for_indexed_event_index(self, idx):
        idx = self.indexes[idx]
        return self.get_traces_for_event_index(idx)


class ProcessEventsWithLINT(ProcessEvents):

    """Process events using LInear INTerpolation for arrival times.

    This is a subclass of :class:`ProcessEvents`.  Use a linear
    interpolation method to determine the arrival times of particles.

    """

    def _reconstruct_time_from_trace(self, trace, baseline):
        """Reconstruct time of measurement from a trace (LINT timings).

        This method is doing the hard work.

        :param trace: array containing pulseheight values.
        :param baseline: baseline of the trace
        :returns: arrival time

        """
        threshold = baseline + ADC_THRESHOLD

        # FIXME: apparently, there are a few bugs here. I see, in my
        # cluster reconstruction analysis, timings like -inf and
        # -something. Guesses: sometimes y0 == y1, and sometimes y1 < y0.

        value = np.nan
        for i, t in enumerate(trace):
            if t >= threshold:
                x0, x1 = i - 1, i
                y0, y1 = trace[x0], trace[x1]
                value = 1. * (threshold - y0) / (y1 - y0) + x0
                break

        return value * ADC_TIME_PER_SAMPLE


class ProcessIndexedEventsWithLINT(ProcessIndexedEvents, ProcessEventsWithLINT):

    """Process a subset of events using LInear INTerpolation.

    This is a subclass of :class:`ProcessIndexedEvents` and
    :class:`ProcessEventsWithLint`.

    """
    pass


class ProcessEventsWithoutTraces(ProcessEvents):

    """Process events without traces

    This is a subclass of :class:`ProcessEvents`.  Processing events
    without considering traces will invalidate the arrival time
    information.  However, for some analyses it is not necessary to obtain
    this information.  Ignoring the traces will then greatly decrease
    processing time and data size.

    """

    def _store_results_from_traces(self):
        """Fake storing results from traces."""

        pass

class ProcessIndexedEventsWithoutTraces(ProcessEventsWithoutTraces,
                                        ProcessIndexedEvents):

    """Process a subset of events without traces

    This is a subclass of :class:`ProcessIndexedEvents` and
    :class:`ProcessEventsWithoutTraces`.  Processing events without
    considering traces will invalidate the arrival time information.
    However, for some analyses it is not necessary to obtain this
    information.  Ignoring the traces will then greatly decrease
    processing time and data size.

    """
    pass