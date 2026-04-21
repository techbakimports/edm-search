"""
Classificador de gêneros eletrônicos.
Fase 1: rule-based com scoring por features.
Fase 3: ML (Random Forest) treinado com dataset rotulado.
"""
import os
import json
import numpy as np
from config import GENRE_TAXONOMY, BPM_TOLERANCE, SPECTRAL_THRESHOLDS


def _derive_tags(features: dict) -> set:
    """Converte features numéricas em tags qualitativas para matching."""
    tags = set()
    t = SPECTRAL_THRESHOLDS

    rms = features.get('rms_mean', 0)
    if rms >= t['high_energy_rms']:
        tags.add('high_energy')
    elif rms <= t['low_energy_rms']:
        tags.add('low_energy')
    else:
        tags.add('mid_energy')

    centroid = features.get('spectral_centroid_mean', 0)
    if centroid >= t['bright_centroid']:
        tags.add('bright')
    elif centroid <= t['dark_centroid']:
        tags.add('dark')

    bass_ratio = features.get('bass_ratio', 0)
    if bass_ratio >= 0.55:
        tags.add('bass_heavy')

    zcr = features.get('zcr_mean', 0)
    if zcr >= t['high_zcr']:
        tags.add('noisy')
        tags.add('distorted')

    percussive = features.get('percussive_ratio', 0)
    if percussive >= 0.6:
        tags.add('percussive')
    elif percussive <= 0.3:
        tags.add('acoustic')

    # Vocal heavy: alta energia no mid com chroma forte (indica harmonia vocal)
    chroma_std = features.get('chroma_std', 0)
    mid_ratio = features.get('energy_mid', 0) / (features.get('energy_bass', 1) or 1)
    if chroma_std >= 0.18 and mid_ratio >= 1.5:
        tags.add('vocal_heavy')

    consistency = features.get('tempo_consistency', 1.0)
    if consistency <= 0.05:
        tags.add('repetitive')
    elif consistency >= 0.2:
        tags.add('chaotic')
        tags.add('glitchy')

    contrast = features.get('spectral_contrast_mean', 0)
    if contrast >= 20:
        tags.add('punchy')

    chroma_std = features.get('chroma_std', 0)
    if chroma_std >= 0.2:
        tags.add('melodic')

    return tags


# Tags que começam com "no_" são exclusões: se a faixa tiver aquela tag, penaliza forte
_NEGATION_PREFIX = "no_"


def _score_candidate(bpm: float, features: dict, genre_entry: tuple) -> float:
    """Calcula um score de 0–1 para o quão bem a faixa se encaixa num gênero."""
    _, _, bpm_min, bpm_max, genre_tags = genre_entry

    # Score de BPM (0 se fora da janela, gradual dentro)
    bpm_center = (bpm_min + bpm_max) / 2
    bpm_range  = (bpm_max - bpm_min) / 2 + BPM_TOLERANCE
    bpm_dist   = abs(bpm - bpm_center)
    if bpm_dist > bpm_range:
        return 0.0
    bpm_score = 1.0 - (bpm_dist / bpm_range)

    faixa_tags = _derive_tags(features)

    # Separar tags positivas e negativas (exclusões)
    positive_tags = [t for t in genre_tags if not t.startswith(_NEGATION_PREFIX)]
    exclusion_tags = [t[len(_NEGATION_PREFIX):] for t in genre_tags if t.startswith(_NEGATION_PREFIX)]

    # Penalidade por contradição: cada exclusão violada corta 35% do score final
    contradiction_penalty = 1.0
    for ex in exclusion_tags:
        if ex in faixa_tags:
            contradiction_penalty *= 0.35

    # Score de tags positivas
    if not positive_tags:
        tag_score = 0.5
    else:
        matches = sum(1 for t in positive_tags if t in faixa_tags)
        tag_score = matches / len(positive_tags)

    # Penalidade por BPM range muito amplo (gêneros vagos recebem menos crédito)
    bpm_range_penalty = 1.0 - min((bpm_max - bpm_min) / 300, 0.4)

    raw = bpm_score * 0.55 + tag_score * 0.45
    return raw * contradiction_penalty * bpm_range_penalty


def classify_rule_based(features: dict, top_n: int = 5) -> list[dict]:
    """Classifica usando regras BPM + features espectrais. Retorna top_n candidatos."""
    bpm = features.get('bpm', 0)
    scores = []

    for entry in GENRE_TAXONOMY:
        genre, subgenre, bpm_min, bpm_max, tags = entry
        score = _score_candidate(bpm, features, entry)
        if score > 0:
            scores.append({
                'genre': genre,
                'subgenre': subgenre,
                'score': round(score, 3),
                'bpm_range': f"{bpm_min}–{bpm_max}",
                'method': 'rule-based',
            })

    scores.sort(key=lambda x: x['score'], reverse=True)
    return scores[:top_n]


def classify_ml(features: dict, model_path: str = 'model.pkl') -> list[dict]:
    """Classifica usando modelo ML treinado. Requer treino prévio com train.py."""
    try:
        import pickle
        from sklearn.preprocessing import StandardScaler

        with open(model_path, 'rb') as f:
            bundle = pickle.load(f)

        model = bundle['model']
        scaler = bundle['scaler']
        feature_names = bundle['feature_names']
        label_names = bundle['label_names']

        vec = np.array([[features.get(k, 0) for k in feature_names]])
        vec_scaled = scaler.transform(vec)

        probs = model.predict_proba(vec_scaled)[0]
        results = []
        for label, prob in zip(label_names, probs):
            genre, subgenre = label.split('|')
            results.append({
                'genre': genre,
                'subgenre': subgenre,
                'score': round(float(prob), 3),
                'method': 'ml',
            })

        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:5]

    except FileNotFoundError:
        return []


def classify(features: dict, use_ml: bool = True) -> dict:
    """
    Ponto de entrada principal.
    Usa ML se o modelo existir, senão usa rule-based.
    Sempre retorna os candidatos rule-based junto para comparação.
    """
    rule_results = classify_rule_based(features)
    ml_results = []

    if use_ml and os.path.exists('model.pkl'):
        ml_results = classify_ml(features)

    primary = ml_results if ml_results else rule_results
    top = primary[0] if primary else {'genre': 'Desconhecido', 'subgenre': None, 'score': 0}

    return {
        'genre': top['genre'],
        'subgenre': top['subgenre'],
        'confidence': top['score'],
        'method': top.get('method', 'rule-based'),
        'candidates': primary,
        'rule_based_candidates': rule_results,
    }