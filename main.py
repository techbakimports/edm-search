"""
EDM Analyzer — Identificação de BPM e gêneros musicais.

Uso:
    python main.py <arquivo>                    # Analisa um arquivo
    python main.py <pasta>                      # Analisa todos os arquivos da pasta
    python main.py <arquivo> --plot             # Mostra visualização gráfica
    python main.py <pasta>   --export csv|json  # Exporta resultados
    python main.py --compare <arq1> <arq2>      # Compara duas músicas
    python main.py --gui                        # Abre a interface gráfica
"""
import argparse
import os
import json
import csv
import sys
import math
from datetime import datetime

import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.columns import Columns
from rich.text import Text
from rich import box

from analyzer import analyze_file
from classifier import classify
from config import SUPPORTED_FORMATS

console = Console()

BLOCKS = " ▁▂▃▄▅▆▇█"


def _bar(value: float, max_val: float, width: int = 20, color: str = "cyan") -> Text:
    """Renderiza uma barra Unicode proporcional ao valor."""
    ratio = min(value / max_val, 1.0) if max_val > 0 else 0
    filled = int(ratio * width)
    remainder = ratio * width - filled
    block_idx = int(remainder * 8)
    bar_str = "█" * filled + (BLOCKS[block_idx] if filled < width else "") + "░" * (width - filled - (1 if filled < width else 0))
    pct = f"{ratio * 100:5.1f}%"
    t = Text()
    t.append(bar_str[:width], style=color)
    t.append(f"  {pct}", style="dim")
    return t


def print_spectrum_bars(features: dict):
    """Exibe barras de espectro de frequência e waveform no terminal."""
    bands = [
        ("Sub-bass  20–80Hz  ", features.get('energy_sub_bass', 0), "red"),
        ("Bass     80–300Hz  ", features.get('energy_bass', 0),     "yellow"),
        ("Low-mid  300–1kHz  ", features.get('energy_mid', 0) * 0.4,"green"),
        ("Mid       1–3kHz   ", features.get('energy_mid', 0) * 0.6,"cyan"),
        ("High-mid  3–8kHz   ", features.get('energy_high', 0) * 0.6,"blue"),
        ("High      8–16kHz  ", features.get('energy_high', 0) * 0.4,"magenta"),
    ]

    max_val = max(v for _, v, _ in bands) or 1.0

    console.print("\n[bold white]  Espectro de Frequências[/bold white]")
    console.print("  " + "─" * 42)
    for label, value, color in bands:
        bar = _bar(value, max_val, width=24, color=color)
        row = Text(f"  {label} ")
        row.append_text(bar)
        console.print(row)

    # Mini waveform de amplitude por segmentos
    rms_mean = features.get('rms_mean', 0)
    rms_std  = features.get('rms_std', 0)
    onset    = features.get('onset_strength_mean', 0)
    onset_std= features.get('onset_strength_std', 0)

    # Simula 40 amostras de amplitude variando em torno da média
    rng = np.random.default_rng(seed=42)
    samples = np.clip(rng.normal(rms_mean, rms_std * 0.5, 40), 0, None)
    max_s = samples.max() or 1
    waveform = ""
    for s in samples:
        idx = int((s / max_s) * 8)
        waveform += BLOCKS[idx]

    console.print(f"\n[bold white]  Waveform (amplitude média)[/bold white]")
    console.print("  " + "─" * 42)
    console.print(f"  [cyan]{waveform}[/cyan]")
    console.print(f"  RMS médio: [yellow]{rms_mean:.4f}[/yellow]  "
                  f"Onset strength: [yellow]{onset:.2f}[/yellow]  "
                  f"Variação: [yellow]±{onset_std:.2f}[/yellow]\n")


def format_duration(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def compare_tracks(path1: str, path2: str):
    """Analisa duas faixas lado a lado e mostra as diferenças."""
    console.print(f"\n[bold cyan]Comparando faixas...[/bold cyan]\n")

    results = []
    for path in [path1, path2]:
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), transient=True, console=console) as p:
            p.add_task(os.path.basename(path), total=None)
            f = analyze_file(path)
            c = classify(f)
        results.append((f, c))

    (f1, c1), (f2, c2) = results

    table = Table(title="Comparação", box=box.ROUNDED, border_style="cyan")
    table.add_column("Atributo",  style="dim",    width=20)
    table.add_column(f"[cyan]{f1['file_name'][:30]}[/cyan]", justify="center")
    table.add_column(f"[magenta]{f2['file_name'][:30]}[/magenta]", justify="center")
    table.add_column("Diferença", justify="center", style="yellow")

    def diff(a, b, fmt=".1f", unit=""):
        d = b - a
        sign = "+" if d > 0 else ""
        return f"{sign}{d:{fmt}}{unit}"

    bpm1, bpm2 = f1['bpm'], f2['bpm']
    table.add_row("BPM",        f"{bpm1:.1f}",   f"{bpm2:.1f}",   diff(bpm1, bpm2, ".1f"))
    table.add_row("Tom",        f1['dominant_key'], f2['dominant_key'], "—")
    table.add_row("Duração",    format_duration(f1['duration_seconds']), format_duration(f2['duration_seconds']), "—")
    table.add_row("Gênero",     c1['genre'],     c2['genre'],     "—")
    table.add_row("Subgênero",  c1['subgenre'] or "—", c2['subgenre'] or "—", "—")
    table.add_row("Confiança",  f"{c1['confidence']:.0%}", f"{c2['confidence']:.0%}", "—")

    e1_bass  = f1['energy_sub_bass'] + f1['energy_bass']
    e2_bass  = f2['energy_sub_bass'] + f2['energy_bass']
    e1_high  = f1['energy_high']
    e2_high  = f2['energy_high']
    table.add_row("Graves",     f"{f1['bass_ratio']:.0%}", f"{f2['bass_ratio']:.0%}", diff(f1['bass_ratio'], f2['bass_ratio'], ".0%", ""))
    table.add_row("Brilho",     f"{f1['spectral_centroid_mean']:.0f}Hz", f"{f2['spectral_centroid_mean']:.0f}Hz",
                  diff(f1['spectral_centroid_mean'], f2['spectral_centroid_mean'], ".0f", "Hz"))
    table.add_row("Energia RMS",f"{f1['rms_mean']:.4f}", f"{f2['rms_mean']:.4f}", diff(f1['rms_mean'], f2['rms_mean'], ".4f"))
    table.add_row("Percussão",  f"{f1['percussive_ratio']:.2f}", f"{f2['percussive_ratio']:.2f}", diff(f1['percussive_ratio'], f2['percussive_ratio'], ".2f"))

    console.print(table)

    console.print("\n[bold white]  Espectro — Faixa 1[/bold white]")
    print_spectrum_bars(f1)
    console.print("[bold white]  Espectro — Faixa 2[/bold white]")
    print_spectrum_bars(f2)


def print_result(features: dict, classification: dict):
    genre = classification.get('genre', '?')
    subgenre = classification.get('subgenre') or '—'
    confidence = classification.get('confidence', 0)
    method = classification.get('method', 'rule-based')
    bpm = features.get('bpm', 0)
    key = features.get('dominant_key', '?')
    duration = features.get('duration_seconds', 0)
    bass_ratio = features.get('bass_ratio', 0)
    rms = features.get('rms_mean', 0)

    title = f"[bold cyan]{features.get('file_name', '')}[/bold cyan]"
    console.print(Panel(title, expand=False, border_style="cyan"))

    # Info principal
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="dim", width=18)
    table.add_column(style="bold white")

    table.add_row("BPM",        f"[bold yellow]{bpm:.1f}[/bold yellow]")
    table.add_row("Tom",        f"[magenta]{key}[/magenta]")
    table.add_row("Duração",    format_duration(duration))
    table.add_row("Gênero",     f"[bold green]{genre}[/bold green]")
    table.add_row("Subgênero",  f"[green]{subgenre}[/green]")
    table.add_row("Confiança",  f"{confidence:.0%} [dim]({method})[/dim]")
    table.add_row("Graves",     f"{bass_ratio:.0%} do espectro")
    table.add_row("Energia RMS",f"{rms:.4f}")

    console.print(table)
    print_spectrum_bars(features)

    # Candidatos alternativos
    candidates = classification.get('candidates', [])
    if len(candidates) > 1:
        console.print("[dim]Outros candidatos:[/dim]")
        alt_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        alt_table.add_column("#", style="dim", width=3)
        alt_table.add_column("Gênero", style="white")
        alt_table.add_column("Subgênero", style="white")
        alt_table.add_column("Score", style="yellow")
        alt_table.add_column("BPM Range", style="dim")

        for i, c in enumerate(candidates[:5], 1):
            style = "bold" if i == 1 else ""
            alt_table.add_row(
                str(i),
                f"[{style}]{c['genre']}[/{style}]" if style else c['genre'],
                c.get('subgenre') or '—',
                f"{c['score']:.3f}",
                c.get('bpm_range', '—'),
            )
        console.print(alt_table)

    console.print()


def analyze_single(path: str, plot: bool = False) -> dict:
    """Analisa um único arquivo e retorna o resultado."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]Analisando {task.description}..."),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task(os.path.basename(path), total=None)
        features = analyze_file(path)
        classification = classify(features)

    print_result(features, classification)

    if plot:
        try:
            from visualizer import plot_analysis
            console.print("[dim]Abrindo visualização...[/dim]")
            plot_analysis(path, features, classification)
        except Exception as e:
            console.print(f"[yellow]Visualização indisponível: {e}[/yellow]")

    return {'features': features, 'classification': classification}


def analyze_batch(folder: str, export: str = None, plot: bool = False) -> list[dict]:
    """Analisa todos os arquivos de áudio de uma pasta."""
    supported = set(SUPPORTED_FORMATS)
    files = [
        os.path.join(folder, f)
        for f in sorted(os.listdir(folder))
        if os.path.splitext(f)[1].lower() in supported
    ]

    if not files:
        console.print(f"[red]Nenhum arquivo de áudio encontrado em: {folder}[/red]")
        return []

    console.print(f"[cyan]{len(files)} arquivo(s) encontrado(s).[/cyan]\n")

    results = []
    errors = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Analisando...", total=len(files))

        for path in files:
            progress.update(task, description=os.path.basename(path))
            try:
                features = analyze_file(path)
                classification = classify(features)
                results.append({'features': features, 'classification': classification})
            except Exception as e:
                errors.append((os.path.basename(path), str(e)))
                console.print(f"[red]Erro em {os.path.basename(path)}: {e}[/red]")
            progress.advance(task)

    console.print()

    # Tabela de resumo
    summary = Table(title="Resultado da Análise em Lote", box=box.ROUNDED, border_style="cyan")
    summary.add_column("Arquivo", style="white", max_width=40)
    summary.add_column("BPM", style="yellow", justify="right")
    summary.add_column("Tom", style="magenta", justify="center")
    summary.add_column("Gênero", style="green")
    summary.add_column("Subgênero", style="green")
    summary.add_column("Confiança", style="cyan", justify="right")

    for r in results:
        f = r['features']
        c = r['classification']
        summary.add_row(
            f.get('file_name', ''),
            f"{f.get('bpm', 0):.1f}",
            f.get('dominant_key', '?'),
            c.get('genre', '?'),
            c.get('subgenre') or '—',
            f"{c.get('confidence', 0):.0%}",
        )

    console.print(summary)

    if errors:
        console.print(f"\n[yellow]{len(errors)} arquivo(s) com erro:[/yellow]")
        for name, err in errors:
            console.print(f"  [red]•[/red] {name}: {err}")

    if export:
        _export_results(results, folder, export)

    if plot:
        for r in results:
            try:
                from visualizer import plot_analysis
                path = r['features']['file_path']
                save = os.path.splitext(path)[0] + '_analysis.png'
                plot_analysis(path, r['features'], r['classification'], save_path=save)
                console.print(f"[dim]Imagem salva: {os.path.basename(save)}[/dim]")
            except Exception as e:
                console.print(f"[yellow]Visualização falhou: {e}[/yellow]")

    return results


def _export_results(results: list[dict], folder: str, fmt: str):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(folder, f"edm_analysis_{ts}.{fmt}")

    if fmt == 'json':
        rows = []
        for r in results:
            f, c = r['features'], r['classification']
            rows.append({
                'file': f.get('file_name'),
                'bpm': f.get('bpm'),
                'key': f.get('dominant_key'),
                'duration': f.get('duration_seconds'),
                'genre': c.get('genre'),
                'subgenre': c.get('subgenre'),
                'confidence': c.get('confidence'),
                'method': c.get('method'),
            })
        with open(out_path, 'w', encoding='utf-8') as fp:
            json.dump(rows, fp, ensure_ascii=False, indent=2)

    elif fmt == 'csv':
        with open(out_path, 'w', newline='', encoding='utf-8') as fp:
            writer = csv.DictWriter(fp, fieldnames=[
                'file', 'bpm', 'key', 'duration', 'genre', 'subgenre', 'confidence', 'method'
            ])
            writer.writeheader()
            for r in results:
                f, c = r['features'], r['classification']
                writer.writerow({
                    'file': f.get('file_name'),
                    'bpm': f.get('bpm'),
                    'key': f.get('dominant_key'),
                    'duration': f.get('duration_seconds'),
                    'genre': c.get('genre'),
                    'subgenre': c.get('subgenre'),
                    'confidence': c.get('confidence'),
                    'method': c.get('method'),
                })

    console.print(f"[green]Exportado: {out_path}[/green]")


def main():
    parser = argparse.ArgumentParser(
        description="Music Analyzer — Identificação de BPM e gêneros musicais.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('input', nargs='?', help="Arquivo de áudio ou pasta")
    parser.add_argument('--plot',    action='store_true', help="Exibe visualização gráfica")
    parser.add_argument('--export',  choices=['csv', 'json'], help="Exporta resultados (pasta)")
    parser.add_argument('--compare', nargs=2, metavar=('ARQUIVO1', 'ARQUIVO2'), help="Compara duas músicas")
    parser.add_argument('--gui',     action='store_true', help="Abre a interface gráfica")
    args = parser.parse_args()

    if args.gui:
        from gui import launch
        launch()
        return

    if args.compare:
        for p in args.compare:
            if not os.path.exists(p):
                console.print(f"[red]Arquivo não encontrado: {p}[/red]")
                sys.exit(1)
        compare_tracks(args.compare[0], args.compare[1])
        return

    if not args.input:
        parser.print_help()
        sys.exit(0)

    if not os.path.exists(args.input):
        console.print(f"[red]Caminho não encontrado: {args.input}[/red]")
        sys.exit(1)

    if os.path.isfile(args.input):
        analyze_single(args.input, plot=args.plot)
    elif os.path.isdir(args.input):
        analyze_batch(args.input, export=args.export, plot=args.plot)
    else:
        console.print("[red]Entrada inválida.[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
