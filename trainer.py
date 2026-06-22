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
    'fourier_tempogram_mean', 'fourier_tempogram_std', 'fourier_tempogram_max',
    'ac_tempogram_mean', 'ac_tempogram_std', 'ac_tempogram_max',
] + [f'mfcc_{i}_mean' for i in range(1, 14)] + [f'mfcc_{i}_std' for i in range(1, 14)]


CHECKPOINT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'train_checkpoint.pkl')


def save_checkpoint(dataset_dir: str, items: list, done_paths: set,
                    X: list, y_labels: list, errors: int):
    data = {
        'version': 1,
        'dataset_dir': os.path.normpath(dataset_dir),
        'items': items,
        'done_paths': done_paths,
        'X': X,
        'y_labels': y_labels,
        'errors': errors,
    }
    tmp = CHECKPOINT_PATH + '.tmp'
    with open(tmp, 'wb') as f:
        pickle.dump(data, f)
    os.replace(tmp, CHECKPOINT_PATH)


def load_checkpoint(dataset_dir: str) -> dict | None:
    if not os.path.exists(CHECKPOINT_PATH):
        return None
    try:
        with open(CHECKPOINT_PATH, 'rb') as f:
            data = pickle.load(f)
        if data.get('version') != 1:
            return None
        if os.path.normpath(data.get('dataset_dir', '')) != os.path.normpath(dataset_dir):
            return None
        return data
    except Exception as e:
        console.print(f"[yellow]Checkpoint corrompido, recomeçando: {e}[/yellow]")
        return None


def clear_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        os.remove(CHECKPOINT_PATH)


def scan_dataset(dataset_dir: str) -> list[tuple[str, str, str]]:
    """Escaneia pasta e retorna [(caminho, genre, subgenre)].

    Suporta 2 ou 3 níveis automaticamente:
      3 níveis: dataset_dir/genre/subgenre/arquivo.mp3
      2 níveis: dataset_dir/subgenre/arquivo.mp3  (genre = nome da pasta selecionada)
    """
    items = []
    from config import SUPPORTED_FORMATS
    supported = set(SUPPORTED_FORMATS)
    root_name = os.path.basename(os.path.normpath(dataset_dir))

    for entry in os.listdir(dataset_dir):
        entry_path = os.path.join(dataset_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        sub_entries = os.listdir(entry_path)
        has_subdirs = any(os.path.isdir(os.path.join(entry_path, s)) for s in sub_entries)

        if has_subdirs:
            # 3 níveis: entry = genre, subpastas = subgenres
            for subgenre in sub_entries:
                sub_path = os.path.join(entry_path, subgenre)
                if not os.path.isdir(sub_path):
                    continue
                for f in os.listdir(sub_path):
                    if os.path.splitext(f)[1].lower() in supported:
                        items.append((os.path.join(sub_path, f), entry, subgenre))
        else:
            # 2 níveis: root_name = genre, entry = subgenre
            for f in sub_entries:
                if os.path.splitext(f)[1].lower() in supported:
                    items.append((os.path.join(entry_path, f), root_name, entry))
    return items


def train(dataset_dir: str, output_path: str = 'model.pkl', n_estimators: int = 200,
          checkpoint_interval: int = 50):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score

    ckpt = load_checkpoint(dataset_dir)
    if ckpt:
        items      = ckpt['items']
        done_paths = ckpt['done_paths']
        X          = ckpt['X']
        y_labels   = ckpt['y_labels']
        errors     = ckpt['errors']
        console.print(f"[yellow]Retomando checkpoint: {len(X)} faixas processadas, "
                      f"{len(items) - len(done_paths)} restantes.[/yellow]")
    else:
        items = scan_dataset(dataset_dir)
        if not items:
            console.print("[red]Nenhuma faixa encontrada no dataset.[/red]")
            return
        console.print(f"[cyan]Encontradas {len(items)} faixas para treino.[/cyan]")
        done_paths = set()
        X, y_labels, errors = [], [], 0

    remaining = [item for item in items if item[0] not in done_paths]

    for i, (path, genre, subgenre) in enumerate(track(remaining, description="Extraindo features...")):
        try:
            features = analyze_file(path)
            X.append([features.get(k, 0) for k in NUMERIC_FEATURES])
            y_labels.append(f"{genre}|{subgenre}")
        except Exception as e:
            errors += 1
            console.print(f"[yellow]Erro em {os.path.basename(path)}: {e}[/yellow]")
        done_paths.add(path)

        if (i + 1) % checkpoint_interval == 0:
            save_checkpoint(dataset_dir, items, done_paths, X, y_labels, errors)

    if errors:
        console.print(f"[yellow]{errors} faixas com erro foram ignoradas.[/yellow]")

    if not X:
        console.print("[red]Nenhuma feature extraída com sucesso. Abortando.[/red]")
        return

    X_arr = np.array(X)
    y_arr = np.array(y_labels)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_arr)

    model = RandomForestClassifier(n_estimators=n_estimators, random_state=42, n_jobs=-1)

    from collections import Counter
    class_counts = Counter(y_labels)
    min_samples = min(class_counts.values())
    cv = min(5, min_samples)
    if cv < 2:
        console.print("[yellow]Poucas amostras por classe para validação cruzada, pulando...[/yellow]")
    else:
        console.print(f"[cyan]Validação cruzada ({cv}-fold)...[/cyan]")
        scores = cross_val_score(model, X_scaled, y_arr, cv=cv)
        console.print(f"[green]Acurácia média: {scores.mean():.2%} ± {scores.std():.2%}[/green]")

    model.fit(X_scaled, y_arr)

    bundle = {
        'model': model,
        'scaler': scaler,
        'feature_names': NUMERIC_FEATURES,
        'label_names': model.classes_.tolist(),
    }
    with open(output_path, 'wb') as f:
        pickle.dump(bundle, f)

    clear_checkpoint()

    console.print(f"[green]Modelo salvo em {output_path}[/green]")
    console.print(f"[dim]Gêneros treinados: {len(set(y_labels))}[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Treina o classificador de gêneros eletrônicos.")
    parser.add_argument('--dataset', required=True, help="Pasta raiz do dataset (genre/subgenre/arquivo)")
    parser.add_argument('--output', default='model.pkl', help="Caminho do modelo gerado")
    parser.add_argument('--estimators', type=int, default=200, help="Número de árvores no Random Forest")
    args = parser.parse_args()

    train(args.dataset, args.output, args.estimators)