"""
Treina o classificador ML com faixas rotuladas.

Uso:
    python trainer.py --dataset ./dataset --output model.pkl

Estrutura esperada do dataset:
    dataset/
        House/Deep House/track1.mp3
        House/Tech House/track2.wav
        Techno/Minimal Techno/track3.flac
        ...
"""
import os
import pickle
import argparse
import numpy as np
from rich.console import Console
from rich.progress import track

from analyzer import analyze_file

console = Console()

NUMERIC_FEATURES = [
    'bpm', 'tempo_consistency',
    'rms_mean', 'rms_std',
    'energy_sub_bass', 'energy_bass', 'energy_mid', 'energy_high',
    'bass_ratio', 'high_ratio',
    'spectral_centroid_mean', 'spectral_centroid_std',
    'spectral_rolloff_mean', 'spectral_bandwidth_mean',
    'spectral_contrast_mean', 'zcr_mean',
    'percussive_ratio', 'onset_strength_mean', 'onset_strength_std',
    'chroma_mean', 'chroma_std',
] + [f'mfcc_{i}_mean' for i in range(1, 14)] + [f'mfcc_{i}_std' for i in range(1, 14)]


def scan_dataset(dataset_dir: str) -> list[tuple[str, str, str]]:
    """Escaneia pasta e retorna [(caminho, genre, subgenre)]."""
    items = []
    supported = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aiff'}
    for genre in os.listdir(dataset_dir):
        genre_path = os.path.join(dataset_dir, genre)
        if not os.path.isdir(genre_path):
            continue
        for subgenre in os.listdir(genre_path):
            sub_path = os.path.join(genre_path, subgenre)
            if not os.path.isdir(sub_path):
                continue
            for f in os.listdir(sub_path):
                if os.path.splitext(f)[1].lower() in supported:
                    items.append((os.path.join(sub_path, f), genre, subgenre))
    return items


def train(dataset_dir: str, output_path: str = 'model.pkl', n_estimators: int = 200):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score

    items = scan_dataset(dataset_dir)
    if not items:
        console.print("[red]Nenhuma faixa encontrada no dataset.[/red]")
        return

    console.print(f"[cyan]Encontradas {len(items)} faixas para treino.[/cyan]")

    X, y_labels = [], []
    errors = []

    for path, genre, subgenre in track(items, description="Extraindo features..."):
        try:
            features = analyze_file(path)
            vec = [features.get(k, 0) for k in NUMERIC_FEATURES]
            X.append(vec)
            y_labels.append(f"{genre}|{subgenre}")
        except Exception as e:
            errors.append((path, str(e)))

    if errors:
        console.print(f"[yellow]{len(errors)} faixas com erro foram ignoradas.[/yellow]")

    X = np.array(X)
    y = np.array(y_labels)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = RandomForestClassifier(n_estimators=n_estimators, random_state=42, n_jobs=-1)

    console.print("[cyan]Validação cruzada (5-fold)...[/cyan]")
    scores = cross_val_score(model, X_scaled, y, cv=5)
    console.print(f"[green]Acurácia média: {scores.mean():.2%} ± {scores.std():.2%}[/green]")

    model.fit(X_scaled, y)

    bundle = {
        'model': model,
        'scaler': scaler,
        'feature_names': NUMERIC_FEATURES,
        'label_names': model.classes_.tolist(),
    }
    with open(output_path, 'wb') as f:
        pickle.dump(bundle, f)

    console.print(f"[green]Modelo salvo em {output_path}[/green]")
    console.print(f"[dim]Gêneros treinados: {len(set(y_labels))}[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Treina o classificador de gêneros eletrônicos.")
    parser.add_argument('--dataset', required=True, help="Pasta raiz do dataset (genre/subgenre/arquivo)")
    parser.add_argument('--output', default='model.pkl', help="Caminho do modelo gerado")
    parser.add_argument('--estimators', type=int, default=200, help="Número de árvores no Random Forest")
    args = parser.parse_args()

    train(args.dataset, args.output, args.estimators)