import os
from dotenv import load_dotenv

# Taxonomia de gêneros e subgêneros da música eletrônica
# (genre, subgenre, bpm_min, bpm_max, tags espectrais)

APP_NAME        = "EDM Analyzer"
APP_VERSION     = "1.0.0"
COMPANY_NAME    = "Techbak Solutions"
APP_DESCRIPTION = "Análise profissional de música eletrônica"
APP_COPYRIGHT   = "© 2026 Techbak Solutions. Todos os direitos reservados."

GENRE_TAXONOMY = [

    # ── AMBIENT / DOWNTEMPO ──────────────────────────────────────────
    ("Ambient",         "Drone",                0,   65,  ["low_energy", "dark", "textural"]),
    ("Ambient",         "Ambient",              0,   80,  ["low_energy", "warm", "textural"]),
    ("Ambient",         "Dark Ambient",         0,   80,  ["low_energy", "dark", "textural", "no_high_energy"]),
    ("Downtempo",       "Chillout",             70,  95,  ["low_energy", "warm", "melodic"]),
    ("Downtempo",       "Trip Hop",             70,  95,  ["mid_energy", "dark", "bass_heavy"]),
    ("Downtempo",       "Lo-fi Hip Hop",        70,  95,  ["low_energy", "warm", "textural"]),
    ("Techno",          "Dub Techno",           100, 122, ["mid_energy", "dark", "repetitive", "bass_heavy"]),
    ("Downtempo",       "Electronica",          80,  120, ["mid_energy", "melodic", "textural"]),

    # ── ELETRÔNICO ALTERNATIVO ───────────────────────────────────────
    ("Eletrônico",      "Synthwave",            90,  130, ["mid_energy", "bright", "melodic", "bass_heavy"]),
    ("Eletrônico",      "Darkwave",             80,  130, ["mid_energy", "dark", "melodic"]),
    ("Eletrônico",      "Vaporwave",            70,  100, ["low_energy", "warm", "textural", "melodic"]),
    ("Eletrônico",      "Lo-fi Electronic",     80,  115, ["mid_energy", "textural", "warm", "bass_heavy"]),
    ("Eletrônico",      "Synth-punk",           90,  140, ["high_energy", "distorted", "bright", "bass_heavy"]),
    ("Eletrônico",      "Chiptune",             100, 160, ["high_energy", "bright", "repetitive"]),
    ("Eletrônico",      "Electro",              110, 135, ["high_energy", "bass_heavy", "punchy"]),
    ("Eletrônico",      "Industrial",           100, 150, ["high_energy", "distorted", "dark", "noisy"]),
    ("Eletrônico",      "EBM",                  115, 145, ["high_energy", "bass_heavy", "dark", "repetitive"]),
    ("Eletrônico",      "Witch House",          75,  110, ["low_energy", "dark", "bass_heavy", "textural"]),
    ("Eletrônico",      "Hyperpop",             130, 165, ["high_energy", "distorted", "bright", "chaotic"]),
    ("Eletrônico",      "IDM",                  80,  160, ["mid_energy", "textural", "glitchy"]),
    ("Eletrônico",      "Electropop",           110, 135, ["mid_energy", "bright", "melodic"]),

    # ── HOUSE ────────────────────────────────────────────────────────
    ("House",           "Deep House",           118, 126, ["mid_energy", "warm", "bass_heavy", "melodic", "no_distorted"]),
    ("House",           "Afro House",           118, 126, ["high_energy", "warm", "percussive"]),
    ("House",           "Organic House",        118, 124, ["mid_energy", "warm", "acoustic", "melodic"]),
    ("House",           "Progressive House",    124, 132, ["high_energy", "melodic", "no_distorted"]),
    ("House",           "Tech House",           126, 134, ["high_energy", "punchy", "bass_heavy"]),
    ("House",           "Electro House",        126, 134, ["high_energy", "bright", "distorted"]),
    ("House",           "Minimal House",        124, 132, ["mid_energy", "repetitive", "sparse"]),
    ("House",           "Chicago House",        118, 130, ["mid_energy", "warm"]),
    ("House",           "Funky House",          120, 128, ["mid_energy", "warm", "percussive"]),
    ("House",           "Acid House",           120, 130, ["high_energy", "acid", "bass_heavy"]),

    # ── TECHNO ───────────────────────────────────────────────────────
    ("Techno",          "Detroit Techno",       130, 145, ["high_energy", "dark", "industrial"]),
    ("Techno",          "Minimal Techno",       126, 138, ["mid_energy", "sparse", "repetitive"]),
    ("Techno",          "Industrial Techno",    138, 155, ["high_energy", "dark", "noisy", "distorted"]),
    ("Techno",          "Acid Techno",          130, 145, ["high_energy", "acid", "repetitive"]),
    ("Techno",          "Hard Techno",          140, 160, ["high_energy", "punchy", "distorted"]),
    ("Techno",          "Hypnotic Techno",      130, 142, ["mid_energy", "repetitive", "dark"]),
    ("Techno",          "Melodic Techno",       128, 138, ["mid_energy", "melodic", "dark"]),

    # ── TRANCE ───────────────────────────────────────────────────────
    ("Trance",          "Progressive Trance",   128, 138, ["high_energy", "melodic", "no_distorted"]),
    ("Trance",          "Uplifting Trance",     136, 148, ["high_energy", "bright", "melodic"]),
    ("Trance",          "Dark Trance",          136, 148, ["high_energy", "dark", "melodic"]),
    ("Trance",          "Tech Trance",          138, 150, ["high_energy", "punchy", "melodic"]),
    ("Trance",          "Acid Trance",          135, 148, ["high_energy", "acid", "melodic"]),
    ("Trance",          "Vocal Trance",         130, 142, ["high_energy", "melodic", "bright"]),

    # ── PSYTRANCE ────────────────────────────────────────────────────
    ("Psytrance",       "Full On",              140, 150, ["high_energy", "acid", "melodic"]),
    ("Psytrance",       "Progressive Psy",      136, 145, ["high_energy", "melodic", "repetitive"]),
    ("Psytrance",       "Dark Psy",             148, 160, ["high_energy", "dark", "chaotic"]),
    ("Psytrance",       "Forest",               148, 158, ["high_energy", "dark", "noisy"]),
    ("Psytrance",       "Suomi",                148, 158, ["high_energy", "chaotic", "acid"]),
    ("Psytrance",       "Zenonesque",           136, 148, ["mid_energy", "dark", "repetitive"]),
    ("Psytrance",       "Hi-Tech",              180, 230, ["high_energy", "chaotic", "distorted"]),

    # ── DRUM AND BASS ─────────────────────────────────────────────────
    ("Drum and Bass",   "Liquid DnB",           160, 175, ["mid_energy", "warm", "melodic", "bass_heavy"]),
    ("Drum and Bass",   "Neurofunk",            168, 178, ["high_energy", "dark", "bass_heavy"]),
    ("Drum and Bass",   "Jump Up",              170, 180, ["high_energy", "punchy", "bass_heavy"]),
    ("Drum and Bass",   "Jungle",               155, 168, ["high_energy", "percussive", "chaotic"]),
    ("Drum and Bass",   "Techstep",             168, 180, ["high_energy", "dark", "industrial"]),
    ("Drum and Bass",   "Minimal DnB",          160, 174, ["mid_energy", "sparse", "bass_heavy"]),
    ("Drum and Bass",   "Halftime",             75,  90,  ["mid_energy", "bass_heavy", "dark"]),

    # ── DUBSTEP / BASS ────────────────────────────────────────────────
    ("Dubstep",         "Classic Dubstep",      135, 145, ["mid_energy", "dark", "bass_heavy"]),
    ("Dubstep",         "Brostep",              138, 150, ["high_energy", "distorted", "bass_heavy"]),
    ("Dubstep",         "Riddim",               140, 150, ["high_energy", "dark", "repetitive", "bass_heavy"]),
    ("Dubstep",         "Melodic Dubstep",      135, 150, ["high_energy", "melodic", "bass_heavy"]),
    ("Bass Music",      "Future Bass",          100, 150, ["high_energy", "bright", "melodic"]),
    ("Bass Music",      "Trap EDM",             65,  90,  ["mid_energy", "bass_heavy", "sparse"]),
    ("Bass Music",      "Grime",                130, 145, ["mid_energy", "dark", "bass_heavy"]),
    ("Bass Music",      "UK Garage",            128, 136, ["mid_energy", "bass_heavy", "percussive"]),

    # ── HARDCORE ──────────────────────────────────────────────────────
    ("Hardcore",        "Happy Hardcore",       160, 185, ["high_energy", "bright", "melodic"]),
    ("Hardcore",        "UK Hardcore",          155, 175, ["high_energy", "bright", "melodic"]),
    ("Hardcore",        "Gabber",               160, 200, ["high_energy", "distorted", "punchy"]),
    ("Hardcore",        "Terrorcore",           180, 250, ["high_energy", "chaotic", "distorted", "noisy"]),
    ("Hardcore",        "Speedcore",            200, 400, ["high_energy", "chaotic", "distorted"]),
    ("Hardcore",        "Frenchcore",           180, 220, ["high_energy", "distorted", "punchy"]),

    # ── EXPERIMENTAL ─────────────────────────────────────────────────
    ("Experimental",    "Breakcore",            150, 300, ["high_energy", "chaotic", "glitchy"]),
    ("Experimental",    "Noise",                0,   999, ["high_energy", "noisy", "chaotic"]),
    ("Experimental",    "Glitch",               80,  160, ["mid_energy", "glitchy", "textural"]),
]

# Tolerância de BPM ao classificar (±)
BPM_TOLERANCE = 8

# Limiares espectrais
SPECTRAL_THRESHOLDS = {
    "high_energy_rms":    0.07,
    "low_energy_rms":     0.025,
    "bright_centroid":    3500,
    "dark_centroid":      2000,
    "bass_heavy_rolloff": 0.55,
    "high_zcr":           0.08,
    "low_zcr":            0.03,

    # warm: timbre suave, dominância de low-mid, sem harshness
    "warm_max_zcr":           0.05,
    "warm_max_high_ratio":    0.20,
    "warm_min_bass_ratio":    0.30,
    "warm_max_centroid":      3000,

    # textural: sons sustentados e não-percussivos, espectro largo
    "textural_max_percussive": 0.45,
    "textural_min_bandwidth":  1500,

    # sparse: poucos eventos, baixa densidade de onsets
    "sparse_max_onset":        1.5,

    # acid: sweeps de filtro ressonante (aproximação via variância do centroid)
    "acid_min_centroid_std":   500,
    "acid_min_bass_ratio":     0.35,
}

SUPPORTED_FORMATS = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aiff")

load_dotenv()

# APIs externas (deixe vazio "" no .env para desativar)
LASTFM_API_KEY        = os.getenv("LASTFM_API_KEY", "")
SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
DISCOGS_KEY           = os.getenv("DISCOGS_KEY", "")
DISCOGS_SECRET        = os.getenv("DISCOGS_SECRET", "")
