#from __future__ import print_function
import os
import mne
import numpy as np
from ptsa.data.TimeSeriesX import TimeSeriesX
from cluster_helper.cluster import cluster_view
from ..log import logger


def run_lcf(events, eeg_dict, ephys_dir, method='fastica', highpass_freq=.5, reref=True, exclude_bad_channels=False,
            iqr_thresh=3, lcf_winsize=.1):
    """
    Runs localized component filtering (DelPozo-Banos & Weidemann, 2017) to clean artifacts from EEG data. Cleaned data
    is written to a new file in the ephys directory for the session. The pipeline is as follows, repeated for each
    EEG recording the session had:

    1) Drop EOG channels and (optionally) bad channels from the data.
    2) High-pass filter the data.
    3) Common average re-reference the data.
    4) Run ICA on the data. A new ICA is fit after each session break, while excluding the time points before the
        session, after the session, and during breaks. The final saved file will still contain all data points, but the
        actual ICA solutions will not be influenced by breaks.
    5) Remove artifacts using LCF, paired with the ICA solutions calculated in #4.
    6) Save a cleaned version of the EEG data to a .fif file.

    To illustrate how this function divides up a session, a session with 2 breaks would have 3 parts:
    1) Start of recording -> End of break 1
    2) End of break 1 -> End of break 2
    3) End of break 2 -> End of recording

    Note that his function uses ipython-cluster-helper to clean all partitions of the session in parallel.

    :param events: Events structure for the session.
    :param eeg_dict: Dictionary mapping the basename of each EEG recording for the session to an MNE raw object
        containing the data from that recording.
    :param ephys_dir: File path of the current_processed EEG directory for the session.
    :param method: String defining which ICA algorithm to use (fastica, infomax, extended-infomax, picard).
    :param highpass_freq: The frequency in Hz at which to high-pass filter the data prior to ICA (recommended >= .5)
    :param reref: If True, common average references the data prior to ICA. If False, no re-referencing is performed.
    :param exclude_bad_channels: If True, excludes bad channels during ICA and leaves them out of the cleaned data.
    :param iqr_thresh: The number of interquartile ranges above the 75th percentile or below the 25th percentile that a
        sample must be for LCF to mark it as artifactual.
    :param lcf_winsize: The width (in seconds) of the LCF dilator and transition windows.
    :return: None
    """

    # Loop over all of the session's EEG recordings
    for eegfile in eeg_dict:

        ##########
        #
        # Initialization
        #
        ##########

        basename = os.path.splitext(eegfile)[0]
        logger.debug('Cleaning data from {}'.format(basename))

        # Make sure LCF hasn't already been run for this file (prevents re-running LCF during math event creation)
        if os.path.exists(os.path.join(ephys_dir, '%s_clean.h5' % basename)):
            continue

        # Select EEG data and events from current recording
        eeg = eeg_dict[eegfile]
        samp_rate = eeg.info['sfreq']
        evs = events[events.eegfile == os.path.join(ephys_dir, eegfile)]

        ##########
        #
        # EEG Pre-Processing
        #
        ##########

        # Drop EOG channels
        eeg.pick_types(eeg=True, eog=False)

        # High-pass filter the data, since LCF will not work properly if the baseline voltage shifts
        #eeg.filter(highpass_freq, None, fir_design='firwin')

        # Load bad channel info
        badchan_file = os.path.join(ephys_dir, '%s_bad_chan.txt' % basename)
        eeg.load_bad_channels(badchan_file)

        # Rereference data using the common average reference
        #if reref:
        #    eeg.set_eeg_reference(projection=False)

        # By default, mne excludes bad channels during ICA. If not intending to exclude bad chans, clear bad chan list.
        if not exclude_bad_channels:
            eeg.info['bads'] = []

        ##########
        #
        # Identification of session breaks
        #
        ##########

        onsets = []
        offsets = []
        sess_start_in_recording = False
        # Mark all time points before and after the session for exclusion
        if evs[0].type == 'SESS_START':
            sess_start_in_recording = True
            onsets.append(0)
            offsets.append(evs[0].eegoffset)
        if evs[-1].type == 'SESS_END':
            onsets.append(evs[-1].eegoffset)
            offsets.append(eeg.n_times - 1)

        # Mark breaks for exclusion
        # Identify break start/stop times. PyEPL used REST_REWET events; UnityEPL uses BREAK_START/STOP.
        rest_rewet_idx = np.where(evs.type == 'REST_REWET')[0]
        break_start_idx = np.where(evs[:-1].type == 'BREAK_START')[0]
        break_stop_idx = np.where(evs[1:].type == 'BREAK_STOP')[0]

        # Handling for PyEPL studies (only break starts are logged)
        if len(rest_rewet_idx > 0):
            onsets = np.concatenate((evs[rest_rewet_idx].eegoffset, onsets))
            for i, idx in enumerate(rest_rewet_idx):
                # If break is the final event in the current recording, set the offset as the final sample
                # Otherwise, set the offset as 5 seconds before the first event following the break
                if len(evs) == idx + 1:
                    o = eeg.n_times - 1
                else:
                    o = int(evs[idx + 1].eegoffset - 5 * samp_rate)
                # Make sure that offsets cannot occur before onsets (happens if a break lasts less than 5 seconds)
                if o <= onsets[i]:
                    o = onsets[i] + 1
                offsets.append(o)

        # Handling for UnityEPL studies (break starts and stops are both logged)
        elif len(break_start_idx) > 0:
            # If the recordings starts in the middle of a break, the first event will be a break stop.
            # In this case, the break onset is set as the start of the recording.
            if evs[0].type == 'BREAK_STOP':
                onsets.append(0)
                offsets.append(evs[0].eegoffset)
            # If the recording ends in the middle of a break, the last event will be a break start.
            # In this case, set the break offset as the last time point in the recording.
            if evs[-1].type == 'BREAK_START':
                onsets.append(evs[-1].eegoffset)
                offsets.append(eeg.n_times-1)
            # All other break starts and stops are contained fully within the recording
            for i, idx in enumerate(break_start_idx):
                onsets.append(evs[idx].eegoffset)
                offsets.append(evs[break_stop_idx[i]].eegoffset)

        # Annotate the EEG object with the timings of excluded periods (pre-session, post-session, & breaks)
        onsets = np.sort(onsets)
        offsets = np.sort(offsets)
        onset_times = eeg.times[onsets]
        offset_times = eeg.times[offsets]
        durations = offset_times - onset_times
        descriptions = ['bad_break' for _ in onsets]
        annotations = mne.Annotations(eeg.times[onsets], durations, descriptions)
        eeg.set_annotations(annotations)

        # Skip over the offset for the session start when splitting, as we only want to split after breaks
        split_samples = offsets[1:] if sess_start_in_recording else offsets

        # Create file with information on partitions and when breaks occur
        partition_info = [['index', 'start_sample', 'end_sample']]
        start = 0
        for i, stop in enumerate(split_samples):
            partition_info.append([str(i), str(start), str(stop)])  # Add partition number and start and end samples
            for j, onset in enumerate(onsets):  # List session break periods that occur in that partition
                if start <= onset < stop:
                    partition_info[-1].append(str(onsets[j]))
                    partition_info[-1].append(str(offsets[j]))
            start = stop + 1
        # Add extra columns based on the maximum number of break periods that occurs in any one partition
        ncol = max([len(row) for row in partition_info[1:]])
        for i, row in enumerate(partition_info):
            if i == 0:  # Header row
                j = 0
                while len(partition_info[0]) < ncol:
                    partition_info[0] += ['skip_start%i' % j, 'skip_end%i' % j]
                    j += 1
            else:  # Data rows
                partition_info[i] += ['' for _ in range(ncol - len(partition_info[i]))]
            partition_info[i] = '\t'.join(partition_info[i])
        partition_info = '\n'.join(partition_info)

        # Write break/partition info to a tsv file
        breakfile_path = os.path.join(ephys_dir, '%s_breaks.tsv' % basename)
        with open(breakfile_path, 'w') as f:
            f.write(partition_info)
        os.chmod(breakfile_path, 0644)

        ##########
        #
        # ICA + LCF
        #
        ##########

        # Create inputs for running LCF in parallel with ipython-cluster-helper
        inputs = []
        start = 0
        for i, stop in enumerate(split_samples):

            d = dict(index=i, basename=basename, ephys_dir=ephys_dir, method=method, iqr_thresh=iqr_thresh,
                     lcf_winsize=lcf_winsize)
            inputs.append(d)

            # Copy the session data, then crop it down to one part of the session. Save it temporarily for parallel jobs
            split_eeg = eeg.copy()
            split_eeg.crop(split_eeg.times[start], split_eeg.times[stop])
            split_eeg_path = os.path.join(ephys_dir, '%s_%i_raw.fif' % (basename, i))
            split_eeg.save(split_eeg_path, overwrite=True)

            # Set the next part of the EEG recording to begin one sample after the current one
            start = stop + 1

        # Run ICA and then LCF on each part of the sesion in parallel. Sometimes cluster helper returns errors even
        # when successful, so avoid crashing event creation if an error comes up here.
        try:
            with cluster_view(scheduler='sge', queue='RAM.q', num_jobs=len(inputs), cores_per_job=6) as view:
                view.map(run_split_lcf, inputs)
        except Exception:
            logger.warn('Cluster helper returned an error. This may happen even if LCF was successful, so attempting to'
                        ' continue anyway...')
            pass

        # Load cleaned EEG data partitions
        clean = []
        for d in inputs:
            index = d['index']
            clean_eegfile = os.path.join(ephys_dir, '%s_%i_clean_raw.fif' % (basename, index))
            clean.append(mne.io.read_raw_fif(clean_eegfile, preload=True))
            os.remove(clean_eegfile)

        # Concatenate the cleaned partitions of the recording back together
        logger.debug('Constructing cleaned data file for {}'.format(basename))
        clean = mne.concatenate_raws(clean)
        logger.debug('Saving cleaned data for {}'.format(basename))

        ##########
        #
        # Data saving
        #
        ##########

        # Save cleaned version of data to hdf as a TimeSeriesX object
        clean_eegfile = os.path.join(ephys_dir, '%s_clean.h5' % basename)
        TimeSeriesX(clean._data.astype(np.float32), dims=('channels', 'time'),
                    coords={'channels': clean.info['ch_names'], 'time': clean.times,
                            'samplerate': clean.info['sfreq']}).to_hdf(clean_eegfile)
        os.chmod(clean_eegfile, 0644)

        del clean


def run_split_lcf(inputs):
    """
    Runs ICA followed by LCF on one partition of the session. Note that this function has been designed to work as a
    parallel job managed via ipython-cluster-helper. As such, all necessary imports and function declarations have to be
    made within this function, leading to the odd code structure here. This is also why inputs must be passed as a
    dictionary.

    :param inputs: A dictionary specifying the "index" of the partition (for coordination with other parallel jobs), the
        "basename" of the EEG recording, the "ephys_dir" path to the current_processed folder, the "method" of ICA to
        use, the "iqr_thresh" IQR threshold to use for LCF, and the "lcf_winsize" to be used for LCF.
    :return: An MNE raw object containing the cleaned version of the data.
    """
    import os
    import mne
    import pandas as pd
    from ..log import logger

    def lcf(S, feat, sfreq, iqr_thresh, dilator_width, transition_width, ignore=None):
        import numpy as np
        import scipy.signal as sp_signal

        dilator_width = int(dilator_width * sfreq)
        transition_width = int(transition_width * sfreq)

        ##########
        #
        # Classification
        #
        ##########
        # Find interquartile range of each component
        if ignore is None:
            p75 = np.percentile(feat, 75, axis=1)
            p25 = np.percentile(feat, 25, axis=1)
        else:
            p75 = np.percentile(feat[:, ~ignore], 75, axis=1)
            p25 = np.percentile(feat[:, ~ignore], 25, axis=1)
        iqr = p75 - p25

        # Tune artifact thresholds for each component according to the IQR and the iqr_thresh parameter
        pos_thresh = p75 + iqr * iqr_thresh
        neg_thresh = p25 - iqr * iqr_thresh

        # Detect artifacts using the IQR threshold. Dilate the detected zones to account for the mixer transition equation
        ctrl_signal = np.zeros(feat.shape, dtype=float)
        dilator = np.ones(dilator_width)
        for i in range(ctrl_signal.shape[0]):
            ctrl_signal[i, :] = (feat[i, :] > pos_thresh[i]) | (feat[i, :] < neg_thresh[i])
            ctrl_signal[i, :] = np.convolve(ctrl_signal[i, :], dilator, 'same')
        del p75, p25, iqr, pos_thresh, neg_thresh, dilator

        # Binarize signal
        ctrl_signal = (ctrl_signal > 0).astype(float)

        ##########
        #
        # Mixing
        #
        ##########

        # Allocate normalized transition window
        trans_win = sp_signal.hann(transition_width, True)
        trans_win /= trans_win.sum()

        # Pad extremes of control signal
        pad_width = [tuple([0, 0])] * ctrl_signal.ndim
        pad_size = int(transition_width / 2 + 1)
        pad_width[1] = (pad_size, pad_size)
        ctrl_signal = np.pad(ctrl_signal, tuple(pad_width), mode='edge')
        del pad_width

        # Combine the transition window and the control signal to build a final transition-control signal, which can be applied to the components
        for i in range(ctrl_signal.shape[0]):
            ctrl_signal[i, :] = np.convolve(ctrl_signal[i, :], trans_win, 'same')
        del trans_win

        # Remove padding from transition-control signal
        rm_pad_slice = [slice(None)] * ctrl_signal.ndim
        rm_pad_slice[1] = slice(pad_size, -pad_size)
        ctrl_signal = ctrl_signal[rm_pad_slice]
        del rm_pad_slice, pad_size

        # Mix sources with control signal to get cleaned sources
        S_clean = S * (1 - ctrl_signal)

        return S_clean

    def reconstruct_signal(sources, ica):
        import numpy as np

        # Mix sources to translate back into PCA components (PCA components x Time)
        data = np.dot(ica.mixing_matrix_, sources)

        # Mix PCA components to translate back into original EEG channels (Channels x Time)
        data = np.dot(np.linalg.inv(ica.pca_components_), data)

        # Invert transformations that MNE performs prior to PCA
        data += ica.pca_mean_[:, None]
        data *= ica.pre_whitener_

        return data

    # Pull parameters out of input dictionary
    index = inputs['index']
    basename = inputs['basename']
    ephys_dir = inputs['ephys_dir']
    method = inputs['method']
    iqr_thresh = inputs['iqr_thresh']
    lcf_winsize = inputs['lcf_winsize']

    # Load temporary split EEG file and delete it
    split_eeg_path = os.path.join(ephys_dir, '%s_%i_raw.fif' % (basename, index))
    eeg = mne.io.read_raw_fif(split_eeg_path, preload=True)
    os.remove(split_eeg_path)

    # Read locations of breaks and create a mask for use in leaving breaks out of IQR calculation
    ignore = np.zeros(eeg._data.shape[1], dtype=bool)
    breakfile_path = os.path.join(ephys_dir, '%s_breaks.tsv' % basename)
    breaks = pd.read_csv(breakfile_path, delimiter='\t')
    breaks = breaks.loc[index]
    start = int(breaks['start_sample'])
    i = 0
    while 'skip_start%i' % i in breaks and not np.isnan(breaks['skip_start%i' % i]):
        skip_start = breaks['skip_start%i' % i] - start
        skip_end = breaks['skip_end%i'] - start
        ignore[skip_start:skip_end+1] = True
        i += 1

    ######
    # ICA
    ######
    # Run (or load) ICA for the current part of the session
    ica_path = os.path.join(ephys_dir, '%s_%i-ica.fif' % (basename, index))
    if os.path.exists(ica_path):
        logger.debug('Loading ICA (part %i) for %s' % (index, basename))
        ica = mne.preprocessing.read_ica(ica_path)
    else:
        logger.debug('Running ICA (part %i) on %s' % (index, basename))
        ica = mne.preprocessing.ICA(method=method)
        ica.fit(eeg, reject_by_annotation=True)
        ica.save(ica_path)

    ######
    # LCF
    ######
    logger.debug('Running LCF (part %i) on %s' % (index, basename))
    # Convert data to sources
    S = ica.get_sources(eeg)._data
    # Clean artifacts from sources using LCF
    cS = lcf(S, S, eeg.info['sfreq'], iqr_thresh, lcf_winsize, lcf_winsize, ignore=ignore)
    # Reconstruct data from cleaned sources
    eeg._data = reconstruct_signal(cS, ica)

    clean_eegfile = os.path.join(ephys_dir, '%s_%i_clean_raw.fif' % (basename, index))
    eeg.save(clean_eegfile, overwrite=True)
