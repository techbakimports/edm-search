"""
EDM Analyzer — Identificação de BPM e gêneros musicais.

Uso:
    python main.py <arquivo>                    # Analisa um arquivo
    python main.py <pasta>                      # Analisa todos os arquivos da pasta
    python main.py <arquivo> --plot             # Mostra visualização gráfica
    python main.py <pasta>   --export csv|json  # Exporta resultados
    python main.py --compare <arq1> <arq2>      # Compara duas músicas
    python main.py --gui                        # Abre a interface gráfica
    python main.py --tag <arquivo|pasta>              # Taga arquivos pelo nome do arquivo
    python main.py --tag <arquivo|pasta> --dry-run    # Pré-visualiza sem gravar
    python main.py --tag <arquivo|pasta> --no-year    # Não busca ano na internet
    python main.py --tag <arquivo|pasta> --no-cover   # Não baixa capa automática
    python main.py --rename <arquivo|pasta>               # Renomeia arquivos (limpa lixo do nome)
    python main.py --rename <arquivo|pasta> --dry-run     # Pré-visualiza sem renomear
    python main.py --rename <arquivo|pasta> --no-titlecase # Sem Title Case
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

    envelope = features.get('rms_envelope')
    if envelope:
        samples = np.array(envelope)
    else:
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


def _source_table(title: str, color: str) -> Table:
    t = Table(title=title, box=box.SIMPLE_HEAD, border_style=color,
              show_header=False, padding=(0, 2), title_style=f"bold {color}")
    t.add_column(style="dim", width=14)
    t.add_column(style="white")
    return t


def print_result(features: dict, classification: dict):
    bpm      = features.get('bpm', 0)
    key      = features.get('dominant_key', '?')
    duration = features.get('duration_seconds', 0)

    title = f"[bold cyan]{features.get('file_name', '')}[/bold cyan]"
    console.print(Panel(title, expand=False, border_style="cyan"))

    # ── Info de faixa ──────────────────────────────────────────────
    info = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    info.add_column(style="dim", width=14)
    info.add_column(style="bold white")
    info.add_row("BPM",     f"[bold yellow]{bpm:.1f}[/bold yellow]")
    info.add_row("Tom",     f"[magenta]{key}[/magenta]")
    info.add_row("Duração", format_duration(duration))
    console.print(info)

    print_spectrum_bars(features)

    # ── Análise local ──────────────────────────────────────────────
    rule_candidates = (classification.get('rule_based_candidates') or
                       classification.get('candidates') or [])
    top_rule = rule_candidates[0] if rule_candidates else {}

    local = _source_table("Análise local  (áudio)", "cyan")
    local.add_row("Gênero",    f"[bold]{top_rule.get('genre', '—')}[/bold]")
    local.add_row("Subgênero", top_rule.get('subgenre') or '—')
    local.add_row("Confiança", f"{top_rule.get('score', 0):.0%}  "
                               f"[dim]({top_rule.get('method', 'rule-based')})[/dim]")
    console.print(local)

    if len(rule_candidates) > 1:
        cand_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        cand_table.add_column("#",         style="dim", width=3)
        cand_table.add_column("Gênero",    style="white")
        cand_table.add_column("Subgênero", style="white")
        cand_table.add_column("Score",     style="yellow", justify="right")
        cand_table.add_column("BPM",       style="dim")
        for i, c in enumerate(rule_candidates[:3], 1):
            cand_table.add_row(
                str(i), c['genre'], c.get('subgenre') or '—',
                f"{c['score']:.0%}", c.get('bpm_range', '—'),
            )
        console.print(cand_table)

    # ── Spotify ────────────────────────────────────────────────────
    ext = classification.get('lastfm')  # enricher retorna tudo num dict
    sp_feats = (ext or {}).get('spotify_features') or {}

    sp = _source_table("Spotify", "green")
    if ext and ext.get('method') == 'spotify':
        sp.add_row("Gênero",    f"[bold]{ext['genre']}[/bold]")
        sp.add_row("Subgênero", ext.get('subgenre') or '—')
        sp.add_row("Tags",      ", ".join(ext.get('top_tags', [])) or '—')
    else:
        sp.add_row("Gênero",    "[dim]não classificado[/dim]")
        sp.add_row("Subgênero", "[dim]—[/dim]")

    if sp_feats:
        parts = []
        if sp_feats.get('tempo'):       parts.append(f"BPM {sp_feats['tempo']:.0f}")
        if sp_feats.get('energy')       is not None: parts.append(f"energy {sp_feats['energy']:.0%}")
        if sp_feats.get('danceability') is not None: parts.append(f"dance {sp_feats['danceability']:.0%}")
        if sp_feats.get('valence')      is not None: parts.append(f"valence {sp_feats['valence']:.0%}")
        sp.add_row("Audio feats", "  ".join(parts) or '—')
    else:
        sp.add_row("Audio feats", "[dim]sem metadados[/dim]")
    console.print(sp)

    # ── Last.fm ────────────────────────────────────────────────────
    lfm = _source_table("Last.fm", "red")
    if ext and ext.get('method') == 'lastfm':
        lfm.add_row("Gênero",    f"[bold]{ext['genre']}[/bold]")
        lfm.add_row("Subgênero", ext.get('subgenre') or '—')
        lfm.add_row("Tags",      ", ".join(ext.get('top_tags', [])) or '—')
    else:
        lfm.add_row("Gênero",    "[dim]não classificado[/dim]")
        lfm.add_row("Subgênero", "[dim]—[/dim]")
        lfm.add_row("Tags",      "[dim]sem metadados / não encontrado[/dim]")
    console.print(lfm)

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


def tag_command(target: str, dry_run: bool, no_year: bool, no_cover: bool, no_genre: bool = False):
    """Executa o auto-tagging a partir do nome dos arquivos."""
    from tagger import tag_file, tag_folder

    fetch_year  = not no_year
    fetch_cover = not no_cover
    fetch_genre = not no_genre

    if os.path.isfile(target):
        results = [tag_file(target, dry_run=dry_run, fetch_year_online=fetch_year,
                            fetch_cover=fetch_cover, fetch_genre=fetch_genre)]
    elif os.path.isdir(target):
        console.print(f"[cyan]Buscando arquivos de áudio em: {target}[/cyan]\n")
        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]{task.description}"),
            console=console,
            transient=True,
        ) as prog:
            prog.add_task("Tagging...", total=None)
            results = tag_folder(target, dry_run=dry_run, fetch_year_online=fetch_year,
                                 fetch_cover=fetch_cover, fetch_genre=fetch_genre)
    else:
        console.print(f"[red]Caminho não encontrado: {target}[/red]")
        sys.exit(1)

    mode_label = "[yellow](dry-run — nenhum arquivo foi alterado)[/yellow]" if dry_run else ""

    table = Table(
        title=f"Auto-tagging {mode_label}",
        box=box.ROUNDED,
        border_style="cyan",
    )
    table.add_column("Arquivo",  style="white",   max_width=35)
    table.add_column("Artista",  style="cyan",    max_width=22)
    table.add_column("Título",   style="magenta", max_width=30)
    table.add_column("Gênero",   style="green",   max_width=22)
    table.add_column("Ano",      style="yellow",  justify="center", width=6)
    table.add_column("Capa",     justify="center", width=5)
    table.add_column("Status",   justify="center", width=10)

    ok = err = skip = 0
    for r in results:
        if r.get('error') and not r['written']:
            status = f"[red]✗ {r['error']}[/red]"
            err += 1
        elif r['written']:
            status = "[green]✓[/green]" if not dry_run else "[yellow]preview[/yellow]"
            ok += 1
        else:
            status = "[dim]—[/dim]"
            skip += 1

        cover_col = "[green]✓[/green]" if r.get('cover_written') else "[dim]—[/dim]"

        table.add_row(
            r.get('file', ''),
            r.get('artist') or '[dim]—[/dim]',
            r.get('title') or '[dim]—[/dim]',
            r.get('genre') or '[dim]—[/dim]',
            r.get('year') or '[dim]—[/dim]',
            cover_col,
            status,
        )

    console.print(table)
    covers_ok = sum(1 for r in results if r.get('cover_written'))
    console.print(
        f"\n  [green]{ok} arquivo(s) tagado(s)[/green]"
        + (f"  [cyan]{covers_ok} capa(s) gravada(s)[/cyan]" if covers_ok else "")
        + (f"  [red]{err} erro(s)[/red]" if err else "")
        + (f"  [dim]{skip} ignorado(s)[/dim]" if skip else "")
    )
    if not no_year and any(r.get('year') in (None, '—') for r in results if r.get('artist')):
        console.print("[dim]  Dica: use --no-year para pular a busca online se estiver offline.[/dim]")


def rename_command(target: str, dry_run: bool, title_case: bool):
    """Renomeia arquivos de áudio limpando lixo do nome."""
    from tagger import rename_file, rename_folder

    if os.path.isfile(target):
        results = [rename_file(target, dry_run=dry_run, title_case=title_case)]
    elif os.path.isdir(target):
        console.print(f"[cyan]Buscando arquivos de áudio em: {target}[/cyan]\n")
        results = rename_folder(target, dry_run=dry_run, title_case=title_case)
    else:
        console.print(f"[red]Caminho não encontrado: {target}[/red]")
        sys.exit(1)

    if not results:
        console.print("[yellow]Nenhum arquivo de áudio encontrado.[/yellow]")
        return

    mode_label = "[yellow](dry-run — nenhum arquivo foi renomeado)[/yellow]" if dry_run else ""

    table = Table(
        title=f"Rename {mode_label}",
        box=box.ROUNDED,
        border_style="cyan",
    )
    table.add_column("Antes",  style="white",   max_width=50)
    table.add_column("Depois", style="cyan",    max_width=50)
    table.add_column("Status", justify="center", width=10)

    renamed = unchanged = errors = 0
    for r in results:
        old = r.get('file', '')
        new = r.get('new_name') or '—'

        if r.get('error') and not r.get('renamed'):
            status = f"[red]✗ {r['error'][:30]}[/red]"
            errors += 1
        elif r.get('renamed'):
            status = "[green]✓[/green]" if not dry_run else "[yellow]preview[/yellow]"
            renamed += 1
        else:
            status = "[dim]inalterado[/dim]"
            unchanged += 1
            new = "[dim]= igual[/dim]"

        # Destaca as diferenças entre antes e depois
        if r.get('renamed') and old != new:
            table.add_row(f"[dim]{old}[/dim]", f"[bold]{new}[/bold]", status)
        else:
            table.add_row(old, new, status)

    console.print(table)
    console.print(
        f"\n  [green]{renamed} renomeado(s)[/green]"
        + (f"  [dim]{unchanged} inalterado(s)[/dim]" if unchanged else "")
        + (f"  [red]{errors} erro(s)[/red]" if errors else "")
    )


def audit_command(dataset_dir: str, threshold: float = 0.8, export_fmt: str = None):
    """Percorre o dataset, classifica com ML e reporta discordâncias de alta confiança."""
    if not os.path.exists('model.pkl'):
        console.print("[red]model.pkl não encontrado. Treine primeiro:[/red]")
        console.print("[dim]  python trainer.py --dataset <pasta>[/dim]")
        sys.exit(1)

    from trainer import scan_dataset
    from classifier import classify_ml

    items = scan_dataset(dataset_dir)
    if not items:
        console.print("[red]Nenhuma faixa encontrada no dataset.[/red]")
        return

    console.print(f"[cyan]Auditando {len(items)} faixas (confiança mínima: {threshold:.0%})...[/cyan]\n")

    suspects, errors = [], 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Auditando...", total=len(items))
        for path, genre, subgenre in items:
            progress.update(task, description=os.path.basename(path))
            try:
                features  = analyze_file(path)
                ml_results = classify_ml(features)
                if ml_results:
                    top  = ml_results[0]
                    conf = top['score']
                    if (top['genre'] != genre or top['subgenre'] != subgenre) and conf >= threshold:
                        suspects.append({
                            'file':              os.path.basename(path),
                            'path':              path,
                            'declared_genre':    genre,
                            'declared_subgenre': subgenre,
                            'model_genre':       top['genre'],
                            'model_subgenre':    top['subgenre'],
                            'confidence':        conf,
                        })
            except Exception:
                errors += 1
            progress.advance(task)

    console.print()

    if not suspects:
        console.print(f"[green]Nenhuma discordância acima de {threshold:.0%}.[/green]")
        if errors:
            console.print(f"[yellow]{errors} arquivo(s) com erro.[/yellow]")
        return

    table = Table(
        title=f"Suspeitos de rótulo errado — {len(suspects)} de {len(items)} faixas",
        box=box.ROUNDED, border_style="yellow",
    )
    table.add_column("Arquivo",          style="white",  max_width=38)
    table.add_column("Pasta (declarado)", style="dim",   max_width=24)
    table.add_column("Modelo diz",        style="yellow", max_width=24)
    table.add_column("Confiança",         style="cyan",  justify="right", width=10)

    for s in suspects:
        table.add_row(
            s['file'],
            f"{s['declared_genre']} / {s['declared_subgenre']}",
            f"{s['model_genre']} / {s['model_subgenre']}",
            f"{s['confidence']:.0%}",
        )

    console.print(table)

    if errors:
        console.print(f"\n[yellow]{errors} arquivo(s) com erro foram ignorados.[/yellow]")

    if export_fmt:
        ts      = datetime.now().strftime('%Y%m%d_%H%M%S')
        out     = os.path.join(dataset_dir, f"audit_{ts}.{export_fmt}")
        if export_fmt == 'csv':
            with open(out, 'w', newline='', encoding='utf-8') as fp:
                writer = csv.DictWriter(fp, fieldnames=list(suspects[0].keys()))
                writer.writeheader()
                writer.writerows(suspects)
        elif export_fmt == 'json':
            with open(out, 'w', encoding='utf-8') as fp:
                json.dump(suspects, fp, ensure_ascii=False, indent=2)
        console.print(f"[green]Relatório exportado: {out}[/green]")


def main():
    parser = argparse.ArgumentParser(
        description="Music Analyzer — Identificação de BPM e gêneros musicais.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('input', nargs='?', help="Arquivo de áudio ou pasta")
    parser.add_argument('--plot',       action='store_true', help="Exibe visualização gráfica")
    parser.add_argument('--export',     choices=['csv', 'json'], help="Exporta resultados")
    parser.add_argument('--compare',    nargs=2, metavar=('ARQUIVO1', 'ARQUIVO2'), help="Compara duas músicas")
    parser.add_argument('--gui',        action='store_true', help="Abre a interface gráfica")
    parser.add_argument('--tag',        metavar='CAMINHO', help="Taga arquivos pelo nome do arquivo")
    parser.add_argument('--dry-run',    action='store_true', help="Com --tag: pré-visualiza sem gravar")
    parser.add_argument('--no-year',    action='store_true', help="Com --tag: não busca ano na internet")
    parser.add_argument('--no-cover',   action='store_true', help="Com --tag: não baixa capa automática")
    parser.add_argument('--no-genre',   action='store_true', help="Com --tag: não busca gênero automaticamente")
    parser.add_argument('--rename',     metavar='CAMINHO', help="Renomeia arquivos limpando lixo do nome")
    parser.add_argument('--no-titlecase', action='store_true', help="Com --rename: não aplica Title Case")
    parser.add_argument('--train',      metavar='DATASET', help="Treina o modelo ML com o dataset (genre/subgenre/arquivo)")
    parser.add_argument('--audit',      metavar='DATASET', help="Audita dataset: detecta faixas possivelmente mal rotuladas")
    parser.add_argument('--threshold',  type=float, default=0.8, help="Com --audit: confiança mínima (padrão: 0.8)")
    parser.add_argument('--estimators', type=int,   default=200, help="Com --train: número de árvores no Random Forest")
    args = parser.parse_args()

    if args.gui:
        from gui import launch
        launch()
        return

    if args.train:
        from trainer import train
        train(args.train, output_path='model.pkl', n_estimators=args.estimators)
        return

    if args.audit:
        audit_command(args.audit, threshold=args.threshold, export_fmt=args.export)
        return

    if args.rename:
        rename_command(args.rename, dry_run=args.dry_run,
                       title_case=not args.no_titlecase)
        return

    if args.tag:
        tag_command(args.tag, dry_run=args.dry_run, no_year=args.no_year,
                    no_cover=args.no_cover, no_genre=args.no_genre)
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
