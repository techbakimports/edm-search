"""
Extração de features de áudio usando librosa.
"""
import contextlib
import librosa
import numpy as np
import soundfile as sf
import os

from config import SUPPORTED_FORMATS


@contextlib.contextmanager
def _silent_stderr():
    """Suprime saída C-level no stderr (avisos do mpg123 sobre tags ID3 malformadas).
    No Windows redireciona tanto o fd POSIX quanto o handle Win32."""
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    old_fd = os.dup(2)
    os.dup2(devnull_fd, 2)

    # Windows: algumas libs C escrevem via SetStdHandle, não pelo fd POSIX
    _win_old_handle = None
    _win_devnull = None
    try:
        import ctypes
        STD_ERROR_HANDLE = -12
        k32 = ctypes.windll.kernel32
        _win_old_handle = k32.GetStdHandle(STD_ERROR_HANDLE)
        _win_devnull = k32.CreateFileW('nul', 0x40000000, 0, None, 3, 0, None)
        k32.SetStdHandle(STD_ERROR_HANDLE, _win_devnull)
    except Exception:
        pass

    try:
        yield
    finally:
        os.dup2(old_fd, 2)
        os.close(old_fd)
        os.close(devnull_fd)
        try:
            if _win_old_handle is not None:
                ctypes.windll.kernel32.SetStdHandle(-12, _win_old_handle)
            if _win_devnull is not None:
                ctypes.windll.kernel32.CloseHandle(_win_devnull)
        except Exception:
            pass


def load_audio(path: str, duration: float = 60.0):
    """Carrega até `duration` segundos do áudio (analisa o meio da faixa, que é mais representativo)."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise ValueError(f"Formato não suportado: {ext}. Suportados: {SUPPORTED_FORMATS}")

    with _silent_stderr():
        total_duration = librosa.get_duration(path=path)
        offset = max(0, (total_duration / 2) - (duration / 2))
        offset = min(offset, max(0, total_duration - duration))
        y, sr = librosa.load(path, sr=22050, offset=offset, duration=duration, mono=True)
        # MP3s VBR sem cabeçalho Xing não suportam seek — tenta do início se retornou vazio
        if len(y) == 0:
            y, sr = librosa.load(path, sr=22050, offset=0, duration=duration, mono=True)
    if len(y) == 0:
        raise ValueError("audio vazio ou corrompido")
    return y, sr, total_duration


def detect_bpm(y, sr) -> tuple[float, np.ndarray]:
    """Detecta BPM e retorna (bpm, beat_frames)."""
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units='frames')
    bpm = float(np.round(tempo, 1))
    return bpm, beat_frames


def extract_features(y, sr) -> dict:
    """Extrai todas as features espectrais e rítmicas."""
    features = {}

    # --- BPM e ritmo ---
    bpm, beat_frames = detect_bpm(y, sr)
    features['bpm'] = bpm

    # Consistência do tempo (baixa = rítmico e regular, alta = irregular)
    if len(beat_frames) > 1:
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        intervals = np.diff(beat_times)
        features['tempo_consistency'] = float(np.std(intervals) / np.mean(intervals)) if np.mean(intervals) > 0 else 1.0
    else:
        features['tempo_consistency'] = 1.0

    # --- Energia ---
    rms = librosa.feature.rms(y=y)[0]
    features['rms_mean'] = float(np.mean(rms))
    features['rms_std'] = float(np.std(rms))

    n_env = 80
    if len(rms) >= n_env:
        idx = np.linspace(0, len(rms) - 1, n_env).astype(int)
        features['rms_envelope'] = [float(v) for v in rms[idx]]
    else:
        features['rms_envelope'] = [float(v) for v in rms]

    # Energia por banda de frequência (sub-bass, bass, mid, high)
    stft = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    def band_energy(f_min, f_max):
        idx = np.where((freqs >= f_min) & (freqs < f_max))[0]
        return float(np.mean(stft[idx])) if len(idx) > 0 else 0.0

    features['energy_sub_bass'] = band_energy(20, 80)
    features['energy_bass'] = band_energy(80, 300)
    features['energy_mid'] = band_energy(300, 3000)
    features['energy_high'] = band_energy(3000, 16000)

    total_energy = sum([
        features['energy_sub_bass'],
        features['energy_bass'],
        features['energy_mid'],
        features['energy_high'],
    ]) or 1.0
    features['bass_ratio'] = (features['energy_sub_bass'] + features['energy_bass']) / total_energy
    features['high_ratio'] = features['energy_high'] / total_energy

    # --- Espectral ---
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    features['spectral_centroid_mean'] = float(np.mean(centroid))
    features['spectral_centroid_std'] = float(np.std(centroid))

    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    features['spectral_rolloff_mean'] = float(np.mean(rolloff))

    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    features['spectral_bandwidth_mean'] = float(np.mean(bandwidth))

    contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
    features['spectral_contrast_mean'] = float(np.mean(contrast))

    # Zero-crossing rate (percussividade / noise)
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    features['zcr_mean'] = float(np.mean(zcr))

    # --- Timbre (MFCC) ---
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    for i, coef in enumerate(mfcc):
        features[f'mfcc_{i+1}_mean'] = float(np.mean(coef))
        features[f'mfcc_{i+1}_std'] = float(np.std(coef))

    # --- Harmonia ---
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    features['chroma_mean'] = float(np.mean(chroma))
    features['chroma_std'] = float(np.std(chroma))

    # Tonalidade dominante
    chroma_avg = np.mean(chroma, axis=1)
    notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    features['dominant_key'] = notes[int(np.argmax(chroma_avg))]

    # --- Percussividade ---
    # margin=2 reduz o kernel do filtro mediano interno (padrão é 1) → menos memória
    harmonic, percussive = librosa.effects.hpss(y, margin=2)
    features['percussive_ratio'] = float(np.mean(np.abs(percussive)) / (np.mean(np.abs(y)) or 1))

    # Onset strength (intensidade das batidas)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    features['onset_strength_mean'] = float(np.mean(onset_env))
    features['onset_strength_std'] = float(np.std(onset_env))

    # --- Tempogram (padrões rítmicos periódicos) ---
    # Fourier tempogram: captura frequências de pulso no domínio espectral
    # Útil para distinguir subgêneros com BPM similar (ex: Techno vs. Trance)
    fourier_tg = np.abs(librosa.feature.fourier_tempogram(onset_envelope=onset_env, sr=sr))
    features['fourier_tempogram_mean'] = float(np.mean(fourier_tg))
    features['fourier_tempogram_std'] = float(np.std(fourier_tg))
    features['fourier_tempogram_max'] = float(np.max(fourier_tg))

    # Autocorrelation tempogram: captura periodicidade do groove
    # Útil para distinguir grooves com subdivisões diferentes (ex: House vs. Techno)
    ac_tg = librosa.feature.tempogram(onset_envelope=onset_env, sr=sr)
    features['ac_tempogram_mean'] = float(np.mean(ac_tg))
    features['ac_tempogram_std'] = float(np.std(ac_tg))
    features['ac_tempogram_max'] = float(np.max(ac_tg))

    return features


def analyze_file(path: str) -> dict:
    """Pipeline completo: carrega o arquivo e retorna todas as features."""
    y, sr, total_duration = load_audio(path)
    features = extract_features(y, sr)
    features['file_path'] = path
    features['file_name'] = os.path.basename(path)
    features['duration_seconds'] = round(total_duration, 1)
    features['sample_rate'] = sr
    return features