"""
Visualizações de waveform, espectro e batidas.
"""
import numpy as np
import librosa
import librosa.display
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import os


def plot_analysis(path: str, features: dict, classification: dict, save_path: str = None):
    """Gera um painel visual completo da análise da faixa."""
    y, sr = librosa.load(path, sr=None, mono=True, duration=60)

    fig = plt.figure(figsize=(14, 10))
    fig.patch.set_facecolor('#0d0d0d')
    gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.35)

    color_main = '#00e5ff'
    color_accent = '#ff4081'
    bg = '#0d0d0d'
    text_color = '#e0e0e0'

    def style_ax(ax, title):
        ax.set_facecolor('#1a1a1a')
        ax.set_title(title, color=text_color, fontsize=9, pad=6)
        ax.tick_params(colors=text_color, labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor('#333333')

    # 1. Waveform
    ax1 = fig.add_subplot(gs[0, :])
    librosa.display.waveshow(y, sr=sr, ax=ax1, color=color_main, alpha=0.8)
    style_ax(ax1, "Waveform")

    # Sobrepor beats
    _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    for bt in beat_times:
        ax1.axvline(x=bt, color=color_accent, alpha=0.3, linewidth=0.5)

    # 2. Espectrograma Mel
    ax2 = fig.add_subplot(gs[1, 0])
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128)
    mel_db = librosa.power_to_db(mel, ref=np.max)
    img = librosa.display.specshow(mel_db, sr=sr, x_axis='time', y_axis='mel', ax=ax2, cmap='magma')
    style_ax(ax2, "Espectrograma Mel")

    # 3. Chromagram
    ax3 = fig.add_subplot(gs[1, 1])
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    librosa.display.specshow(chroma, sr=sr, x_axis='time', y_axis='chroma', ax=ax3, cmap='viridis')
    style_ax(ax3, f"Chromagram — Tom dominante: {features.get('dominant_key', '?')}")

    # 4. Energia por banda
    ax4 = fig.add_subplot(gs[2, 0])
    bands = ['Sub-bass\n(20–80Hz)', 'Bass\n(80–300Hz)', 'Mid\n(300Hz–3kHz)', 'High\n(3–16kHz)']
    energies = [
        features.get('energy_sub_bass', 0),
        features.get('energy_bass', 0),
        features.get('energy_mid', 0),
        features.get('energy_high', 0),
    ]
    max_e = max(energies) or 1
    bars = ax4.bar(bands, [e / max_e for e in energies], color=[color_main, color_accent, '#b39ddb', '#80cbc4'])
    style_ax(ax4, "Distribuição de Energia por Banda")
    ax4.set_ylabel("Energia relativa", color=text_color, fontsize=7)

    # 5. Informações da classificação
    ax5 = fig.add_subplot(gs[2, 1])
    ax5.set_facecolor('#1a1a1a')
    ax5.axis('off')

    genre = classification.get('genre', '?')
    subgenre = classification.get('subgenre', '')
    confidence = classification.get('confidence', 0)
    method = classification.get('method', '')
    bpm = features.get('bpm', 0)
    key = features.get('dominant_key', '?')
    duration = features.get('duration_seconds', 0)

    info_lines = [
        ("Arquivo",    features.get('file_name', '')),
        ("Duração",    f"{int(duration // 60)}:{int(duration % 60):02d}"),
        ("BPM",        f"{bpm:.1f}"),
        ("Tom",        key),
        ("Gênero",     genre),
        ("Subgênero",  subgenre or "—"),
        ("Confiança",  f"{confidence:.0%}"),
        ("Método",     method),
    ]

    for i, (label, value) in enumerate(info_lines):
        y_pos = 0.92 - i * 0.115
        ax5.text(0.0, y_pos, f"{label}:", transform=ax5.transAxes,
                 color='#888888', fontsize=8, va='top')
        ax5.text(0.42, y_pos, str(value), transform=ax5.transAxes,
                 color=text_color, fontsize=8, va='top', fontweight='bold')

    ax5.set_title("Resultado da Análise", color=text_color, fontsize=9, pad=6)

    plt.suptitle(
        f"{features.get('file_name', '')}  —  {genre} / {subgenre}  —  {bpm:.1f} BPM",
        color=color_main, fontsize=11, y=0.98
    )

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=bg)
        plt.close()
    else:
        plt.show()