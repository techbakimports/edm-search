"""
Extração de features de áudio usando librosa.
"""
import contextlib
import librosa
import numpy as np
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
    bpm = round(float(np.atleast_1d(tempo).flat[0]), 1)
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

    # --- Espectral (reutiliza magnitude STFT já calculado) ---
    centroid = librosa.feature.spectral_centroid(S=stft, sr=sr)[0]
    features['spectral_centroid_mean'] = float(np.mean(centroid))
    features['spectral_centroid_std'] = float(np.std(centroid))

    rolloff = librosa.feature.spectral_rolloff(S=stft, sr=sr)[0]
    features['spectral_rolloff_mean'] = float(np.mean(rolloff))

    bandwidth = librosa.feature.spectral_bandwidth(S=stft, sr=sr)[0]
    features['spectral_bandwidth_mean'] = float(np.mean(bandwidth))

    contrast = librosa.feature.spectral_contrast(S=stft, sr=sr)
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
    chroma = librosa.feature.chroma_stft(S=stft**2, sr=sr)
    features['chroma_mean'] = float(np.mean(chroma))
    features['chroma_std'] = float(np.std(chroma))

    # Tonalidade dominante (Krumhansl-Schmuckler key-finding)
    chroma_avg = np.mean(chroma, axis=1)
    _MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    _MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    best_corr, best_key = -2, 'C'
    for i in range(12):
        rotated = np.roll(chroma_avg, -i)
        corr_maj = float(np.corrcoef(rotated, _MAJOR_PROFILE)[0, 1])
        corr_min = float(np.corrcoef(rotated, _MINOR_PROFILE)[0, 1])
        if corr_maj > best_corr:
            best_corr, best_key = corr_maj, notes[i]
        if corr_min > best_corr:
            best_corr, best_key = corr_min, f"{notes[i]}m"
    features['dominant_key'] = best_key

    # --- Percussividade ---
    # margin=2 aumenta a separação harmônica/percussiva (filtro mediano mais agressivo)
    _, percussive = librosa.effects.hpss(y, margin=2)
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


# ── Compatibilidade DJ (mixagem) ──────────────────────────────────────

# Camelot Wheel: mapeia nota >(número, letra)
# Permite identificar tons compatíveis para harmonic mixing.
# Tons compatíveis: mesmo número (A↔B), ±1 no mesmo letra.
_CAMELOT = {
    'C':  (8,  'B'), 'C#': (3,  'B'), 'D':  (10, 'B'), 'D#': (5,  'B'),
    'E':  (12, 'B'), 'F':  (7,  'B'), 'F#': (2,  'B'), 'G':  (9,  'B'),
    'G#': (4,  'B'), 'A':  (11, 'B'), 'A#': (6,  'B'), 'B':  (1,  'B'),
    # Menores (para referência futura — hoje só detectamos a nota, não maior/menor)
    'Am': (8,  'A'), 'A#m': (3, 'A'), 'Bm': (10, 'A'), 'Cm': (5,  'A'),
    'C#m': (12, 'A'), 'Dm': (7, 'A'), 'D#m': (2, 'A'), 'Em': (9,  'A'),
    'Fm': (4,  'A'), 'F#m': (11,'A'), 'Gm': (6,  'A'), 'G#m': (1,  'A'),
}


def _key_distance(key1: str, key2: str) -> int:
    """
    Distância no Camelot wheel entre dois tons.
    0 = mesmo tom, 1 = vizinho harmônico (mix perfeito), 2+ = mais distante.
    Retorna 0-6 (máximo 6 no wheel de 12 posições).
    """
    c1 = _CAMELOT.get(key1)
    c2 = _CAMELOT.get(key2)
    if not c1 or not c2:
        return 3  # desconhecido >neutro
    num1, letter1 = c1
    num2, letter2 = c2
    # Distância circular no wheel (12 posições)
    diff = abs(num1 - num2)
    circular = min(diff, 12 - diff)
    # Mesmo número mas letras diferentes (A↔B) = compatível (energia diferente)
    if circular == 0 and letter1 != letter2:
        return 1
    return circular


def dj_compatibility(fa: dict, fb: dict, ca: dict = None, cb: dict = None) -> dict:
    """
    Calcula compatibilidade entre duas faixas para transição DJ.

    Parâmetros:
        fa, fb: features das faixas A e B (de analyze_file)
        ca, cb: classificação das faixas A e B (de classify, opcional)

    Retorna dict com:
        - score: 0-100 (nota geral)
        - rating: texto ("Perfeita", "Boa", "Possível", "Difícil", "Incompatível")
        - bpm: dict com detalhes do BPM
        - key: dict com detalhes do tom
        - energy: dict com detalhes da energia
        - genre: dict com detalhes do gênero
        - tips: lista de dicas para o DJ
    """
    tips = []

    # ── BPM (peso 35%) ─────────────────────────────────────────────
    bpm_a, bpm_b = fa.get('bpm', 0), fb.get('bpm', 0)
    if bpm_a <= 0 or bpm_b <= 0:
        bpm_score = 0
        tips.append("BPM não detectado em uma das faixas")
    else:
        # Testa BPM direto e dobro/metade (half-time mix)
        diffs = [
            abs(bpm_a - bpm_b) / max(bpm_a, bpm_b),
            abs(bpm_a - bpm_b * 2) / max(bpm_a, bpm_b * 2),
            abs(bpm_a * 2 - bpm_b) / max(bpm_a * 2, bpm_b),
        ]
        best_diff = min(diffs) * 100  # percentual
        if best_diff <= 1:
            bpm_score = 100
        elif best_diff <= 3:
            bpm_score = 90
        elif best_diff <= 6:
            bpm_score = 70
        elif best_diff <= 10:
            bpm_score = 40
        else:
            bpm_score = max(0, 20 - best_diff)

        if best_diff <= 3:
            tips.append(f"BPM próximos ({bpm_a:.0f} >{bpm_b:.0f}) — sync fácil")
        elif best_diff <= 6:
            tips.append(f"BPM compatível com pitch adjust ({best_diff:.1f}%)")
        else:
            tips.append(f"BPMs distantes ({bpm_a:.0f} vs {bpm_b:.0f}) — considere half-time")

    # ── Tom / Harmonia (peso 30%) ──────────────────────────────────
    key_a = fa.get('dominant_key', '?')
    key_b = fb.get('dominant_key', '?')
    key_dist = _key_distance(key_a, key_b)

    if key_dist == 0:
        key_score = 100
        tips.append(f"Mesmo tom ({key_a}) — harmonia perfeita")
    elif key_dist == 1:
        key_score = 95
        tips.append(f"Tons vizinhos ({key_a} >{key_b}) — harmonic mix ideal")
    elif key_dist == 2:
        key_score = 70
        tips.append(f"Tons próximos ({key_a} >{key_b}) — mix funciona com EQ")
    elif key_dist <= 3:
        key_score = 40
        tips.append(f"Tons distantes ({key_a} >{key_b}) — use transição rápida")
    else:
        key_score = max(0, 30 - key_dist * 5)
        tips.append(f"Tons conflitantes ({key_a} >{key_b}) — evite mix longo")

    # ── Energia (peso 20%) ─────────────────────────────────────────
    rms_a = fa.get('rms_mean', 0)
    rms_b = fb.get('rms_mean', 0)
    bass_a = fa.get('bass_ratio', 0)
    bass_b = fb.get('bass_ratio', 0)
    perc_a = fa.get('percussive_ratio', 0)
    perc_b = fb.get('percussive_ratio', 0)

    if rms_a > 0 and rms_b > 0:
        rms_diff = abs(rms_a - rms_b) / max(rms_a, rms_b) * 100
        bass_diff = abs(bass_a - bass_b) * 100
        perc_diff = abs(perc_a - perc_b) * 100
        energy_diff = (rms_diff * 0.4 + bass_diff * 0.3 + perc_diff * 0.3)

        if energy_diff <= 10:
            energy_score = 100
        elif energy_diff <= 25:
            energy_score = 80
        elif energy_diff <= 40:
            energy_score = 60
        else:
            energy_score = max(0, 50 - energy_diff)

        if energy_diff <= 15:
            tips.append("Energia similar — transição suave")
        elif rms_b > rms_a * 1.3:
            tips.append("Faixa B mais intensa — bom para buildup")
        elif rms_a > rms_b * 1.3:
            tips.append("Faixa A mais intensa — Faixa B funciona como cooldown")
    else:
        energy_score = 50

    # ── Gênero (peso 15%) ──────────────────────────────────────────
    if ca and cb:
        genre_a = ca.get('genre', '')
        genre_b = cb.get('genre', '')
        sub_a = ca.get('subgenre') or ''
        sub_b = cb.get('subgenre') or ''

        if genre_a == genre_b and sub_a == sub_b:
            genre_score = 100
            tips.append(f"Mesmo subgênero ({sub_a}) — mix natural")
        elif genre_a == genre_b:
            genre_score = 85
            tips.append(f"Mesmo gênero ({genre_a}) — boa combinação")
        else:
            # Gêneros "próximos" (mesmo universo eletrônico)
            genre_score = 40
            tips.append(f"Gêneros diferentes ({genre_a} >{genre_b}) — mix criativo")
    else:
        genre_score = 50

    # ── Score final (ponderado) ────────────────────────────────────
    score = round(
        bpm_score * 0.35 +
        key_score * 0.30 +
        energy_score * 0.20 +
        genre_score * 0.15
    )
    score = max(0, min(100, score))

    if score >= 85:
        rating = "Perfeita"
    elif score >= 70:
        rating = "Boa"
    elif score >= 50:
        rating = "Possível"
    elif score >= 30:
        rating = "Difícil"
    else:
        rating = "Incompatível"

    return {
        'score': score,
        'rating': rating,
        'bpm':    {'score': bpm_score,    'a': bpm_a,  'b': bpm_b},
        'key':    {'score': key_score,    'a': key_a,  'b': key_b, 'distance': key_dist},
        'energy': {'score': energy_score, 'rms_a': rms_a, 'rms_b': rms_b},
        'genre':  {'score': genre_score},
        'tips':   tips,
    }