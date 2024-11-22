import os
import tensorflow as tf
import numpy as np
from pathlib import Path

# Signal and feature extraction parameters
SAMPLE_RATE = 8000  # 8 kHz
FRAME_SIZE = 0.032  # 32 ms
FRAME_STRIDE = 0.02  # 20 ms
NFFT = 256  # FFT size, equal to frame length
NUM_MEL_BINS = 40  # Assuming unchanged; the paper does not specify this
NUM_MFCCS = 12  # Number of MFCC coefficients remains standard

def preprocess_audio(file_path, label):
    """
    Preprocess an audio file to extract MFCC features.
    Args:
        file_path (str): Path to the audio file.
        label (int): Integer label for the audio file's class.
    Returns:
        tuple: (MFCCs as a TensorFlow tensor, label as an integer).
    """
    # Step 1: Read audio file
    audio_binary = tf.io.read_file(file_path)
    waveform, _ = tf.audio.decode_wav(audio_binary, desired_channels=1)
    waveform = tf.squeeze(waveform, axis=-1)  # Remove channel dimension
    waveform = tf.cast(waveform, tf.float32)

    # Step 2: Pre-emphasis
    pre_emphasis = 0.97
    waveform = tf.concat([[waveform[0]], waveform[1:] - pre_emphasis * waveform[:-1]], axis=0)

    # Step 3: Compute STFT and power spectrogram
    stft = tf.signal.stft(
        waveform,
        frame_length=int(FRAME_SIZE * SAMPLE_RATE),
        frame_step=int(FRAME_STRIDE * SAMPLE_RATE),
        fft_length=NFFT,
        window_fn=tf.signal.hamming_window
    )
    power_spectrogram = tf.square(tf.abs(stft)) / NFFT

    # Step 4: Apply Mel filterbanks
    mel_filterbank = tf.signal.linear_to_mel_weight_matrix(
        num_mel_bins=NUM_MEL_BINS,
        num_spectrogram_bins=stft.shape[-1],
        sample_rate=SAMPLE_RATE,
        lower_edge_hertz=0.0,
        upper_edge_hertz=SAMPLE_RATE / 2
    )
    mel_spectrogram = tf.tensordot(power_spectrogram, mel_filterbank, axes=[-1, 0])
    mel_spectrogram.set_shape(power_spectrogram.shape[:-1] + [NUM_MEL_BINS])

    # Step 5: Convert to log scale
    log_mel_spectrogram = tf.math.log(mel_spectrogram + 1e-6)

    # Step 6: Compute MFCCs
    mfccs = tf.signal.mfccs_from_log_mel_spectrograms(log_mel_spectrogram)
    mfccs = mfccs[..., :NUM_MFCCS]  # Keep only the first NUM_MFCCS coefficients

    return mfccs, label

def prepare_speech_commands_dataset(data_dir, batch_size=32, validation_split=0.2, seed=123):
    """
    Prepare the Speech Commands dataset for training and validation.
    Args:
        data_dir (str): Path to the dataset directory.
        batch_size (int): Batch size for training and validation.
        validation_split (float): Proportion of the data for validation.
        seed (int): Random seed for shuffling and splitting.
    Returns:
        tuple: (training dataset, validation dataset, list of class names).
    """
    data_dir = Path(data_dir)
    class_names = sorted([d.name for d in data_dir.iterdir() if d.is_dir()])
    file_paths = []
    labels = []

    # Collect all file paths and corresponding labels
    for label, class_name in enumerate(class_names):
        class_dir = data_dir / class_name
        for file_path in class_dir.glob("*.wav"):
            file_paths.append(str(file_path))
            labels.append(label)

    # Convert file paths and labels to TensorFlow constants
    file_paths = tf.constant(file_paths)
    labels = tf.constant(labels)

    # Shuffle and split dataset
    dataset_size = len(file_paths)
    val_size = int(dataset_size * validation_split)

    indices = np.arange(dataset_size)
    np.random.seed(seed)
    np.random.shuffle(indices)

    train_indices = indices[val_size:]
    val_indices = indices[:val_size]

    train_file_paths = tf.gather(file_paths, train_indices)
    train_labels = tf.gather(labels, train_indices)

    val_file_paths = tf.gather(file_paths, val_indices)
    val_labels = tf.gather(labels, val_indices)

    # Create TensorFlow datasets
    train_ds = tf.data.Dataset.from_tensor_slices((train_file_paths, train_labels))
    val_ds = tf.data.Dataset.from_tensor_slices((val_file_paths, val_labels))

    # Apply preprocessing
    train_ds = train_ds.map(
        lambda x, y: preprocess_audio(x, y),
        num_parallel_calls=tf.data.AUTOTUNE
    )

    val_ds = val_ds.map(
        lambda x, y: preprocess_audio(x, y),
        num_parallel_calls=tf.data.AUTOTUNE
    )

    # Add channel dimension
    train_ds = train_ds.map(
        lambda x, y: (tf.expand_dims(x, -1), y)
    )

    val_ds = val_ds.map(
        lambda x, y: (tf.expand_dims(x, -1), y)
    )

    # Apply padded batching
    train_ds = train_ds.padded_batch(
        batch_size=batch_size,
        padded_shapes=([None, NUM_MFCCS, 1], []),  # Pad MFCC sequences to [None, NUM_MFCCS] and labels as scalars
        padding_values=(0.0, 0)  # Pad MFCCs with 0.0 and labels with 0
    ).prefetch(tf.data.AUTOTUNE)

    val_ds = val_ds.padded_batch(
        batch_size=batch_size,
        padded_shapes=([None, NUM_MFCCS, 1], []),
        padding_values=(0.0, 0)
    ).prefetch(tf.data.AUTOTUNE)

    return train_ds, val_ds, class_names
