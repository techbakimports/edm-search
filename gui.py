"""
Music Analyzer — Interface Gráfica (DearPyGui 2.x)
Uso: python gui.py  ou  python main.py --gui
"""
import csv
import json
import os
import threading
import queue
import ctypes
from ctypes import wintypes
from datetime import datetime

import numpy as np
import dearpygui.dearpygui as dpg

from analyzer import analyze_file
from classifier import classify
from config import SUPPORTED_FORMATS
from tagger import tag_file, tag_folder

# ── Fila thread-safe ──────────────────────────────────────────────────
_ui_queue: queue.Queue = queue.Queue()

def _ui(fn, *args, **kwargs):
    _ui_queue.put((fn, args, kwargs))

def _process_ui_queue():
    while True:
        try:
            fn, args, kwargs = _ui_queue.get_nowait()
        except queue.Empty:
            break
        try:
            fn(*args, **kwargs)
        except Exception:
            import traceback; traceback.print_exc()

# ── Estado global ─────────────────────────────────────────────────────
W: dict = {}
_tracks        = {"A": None, "B": None}
_batch_results: list[dict] = []
_batch_folder:  str | None = None

# ── Paleta — neon green + purple ─────────────────────────────────────
ACCENT   = (50,  255,  80, 255)   # neon green — primário
ACCENT2  = (170,  40, 255, 255)   # roxo vívido — secundário
SP_GREEN = (30,  215,  96, 255)   # Spotify brand (inalterado)
LFM_RED  = (210,   0,   0, 255)   # Last.fm brand (inalterado)
DIM      = (120, 105, 150, 255)   # roxo-cinza apagado
WHITE    = (215, 210, 238, 255)   # branco com toque roxo
RED      = (255,  70,  80, 255)   # erro
YELLOW   = (255, 200,  50, 255)   # destaques (BPM etc.)
GREEN    = (100, 255, 145, 255)   # verde suave — secundário
BLUE     = (130, 100, 255, 255)   # azul-roxo
PURPLE   = (200, 145, 255, 255)   # roxo suave

# Gradiente do espectro: roxo (sub-bass) → verde neon (altos)
_SPEC_COLORS = [
    (160,   0, 255, 255),
    (190,  55, 255, 255),
    (110,  80, 255, 255),
    ( 50, 205, 160, 255),
    ( 55, 245,  95, 255),
    ( 50, 255,  80, 255),
]

def _fmt_dur(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


# ── Diálogos Windows nativos ──────────────────────────────────────────
def _win_open_file(title="Selecionar arquivo") -> str | None:
    class OPENFILENAME(ctypes.Structure):
        _fields_ = [
            ("lStructSize",       wintypes.DWORD),
            ("hwndOwner",         wintypes.HWND),
            ("hInstance",         wintypes.HINSTANCE),
            ("lpstrFilter",       wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter",    wintypes.DWORD),
            ("nFilterIndex",      wintypes.DWORD),
            ("lpstrFile",         wintypes.LPWSTR),
            ("nMaxFile",          wintypes.DWORD),
            ("lpstrFileTitle",    wintypes.LPWSTR),
            ("nMaxFileTitle",     wintypes.DWORD),
            ("lpstrInitialDir",   wintypes.LPCWSTR),
            ("lpstrTitle",        wintypes.LPCWSTR),
            ("Flags",             wintypes.DWORD),
            ("nFileOffset",       wintypes.WORD),
            ("nFileExtension",    wintypes.WORD),
            ("lpstrDefExt",       wintypes.LPCWSTR),
            ("lCustData",         ctypes.c_ssize_t),
            ("lpfnHook",          ctypes.c_void_p),
            ("lpTemplateName",    wintypes.LPCWSTR),
            ("pvReserved",        ctypes.c_void_p),
            ("dwReserved",        wintypes.DWORD),
            ("FlagsEx",           wintypes.DWORD),
        ]
    buf = ctypes.create_unicode_buffer(32768)
    ofn = OPENFILENAME()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAME)
    ofn.lpstrFilter = "Áudio\0*.mp3;*.wav;*.flac;*.ogg;*.m4a;*.aiff\0Todos\0*.*\0"
    ofn.lpstrFile   = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile    = ctypes.sizeof(buf)
    ofn.lpstrTitle  = title
    ofn.Flags       = 0x00080000 | 0x00001000
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return buf.value
    return None


def _win_open_folder(title="Selecionar pasta") -> str | None:
    class BROWSEINFO(ctypes.Structure):
        _fields_ = [
            ("hwndOwner",      wintypes.HWND),
            ("pidlRoot",       ctypes.c_void_p),
            ("pszDisplayName", ctypes.c_wchar_p),
            ("lpszTitle",      ctypes.c_wchar_p),
            ("ulFlags",        ctypes.c_uint),
            ("lpfn",           ctypes.c_void_p),
            ("lParam",         ctypes.c_long),
            ("iImage",         ctypes.c_int),
        ]

    browse = ctypes.windll.shell32.SHBrowseForFolderW
    browse.restype  = ctypes.c_void_p
    browse.argtypes = [ctypes.POINTER(BROWSEINFO)]

    get_path = ctypes.windll.shell32.SHGetPathFromIDListW
    get_path.restype  = ctypes.c_bool
    get_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]

    disp_buf = ctypes.create_unicode_buffer(32768)
    path_buf = ctypes.create_unicode_buffer(32768)
    bi = BROWSEINFO()
    bi.pszDisplayName = ctypes.cast(disp_buf, ctypes.c_wchar_p)
    bi.lpszTitle      = title
    bi.ulFlags        = 0x0001 | 0x0040  # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE

    ctypes.windll.ole32.CoInitialize(None)
    pidl = browse(ctypes.byref(bi))
    if pidl:
        get_path(ctypes.c_void_p(pidl), path_buf)
        ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
        return path_buf.value or None
    return None


def _win_save_file(title="Salvar", filter_str="CSV\0*.csv\0", default_ext="csv") -> str | None:
    class OPENFILENAME(ctypes.Structure):
        _fields_ = [
            ("lStructSize",       wintypes.DWORD),
            ("hwndOwner",         wintypes.HWND),
            ("hInstance",         wintypes.HINSTANCE),
            ("lpstrFilter",       wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter",    wintypes.DWORD),
            ("nFilterIndex",      wintypes.DWORD),
            ("lpstrFile",         wintypes.LPWSTR),
            ("nMaxFile",          wintypes.DWORD),
            ("lpstrFileTitle",    wintypes.LPWSTR),
            ("nMaxFileTitle",     wintypes.DWORD),
            ("lpstrInitialDir",   wintypes.LPCWSTR),
            ("lpstrTitle",        wintypes.LPCWSTR),
            ("Flags",             wintypes.DWORD),
            ("nFileOffset",       wintypes.WORD),
            ("nFileExtension",    wintypes.WORD),
            ("lpstrDefExt",       wintypes.LPCWSTR),
            ("lCustData",         ctypes.c_ssize_t),
            ("lpfnHook",          ctypes.c_void_p),
            ("lpTemplateName",    wintypes.LPCWSTR),
            ("pvReserved",        ctypes.c_void_p),
            ("dwReserved",        wintypes.DWORD),
            ("FlagsEx",           wintypes.DWORD),
        ]
    buf = ctypes.create_unicode_buffer(32768)
    ofn = OPENFILENAME()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAME)
    ofn.lpstrFilter = filter_str
    ofn.lpstrFile   = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile    = ctypes.sizeof(buf)
    ofn.lpstrTitle  = title
    ofn.lpstrDefExt = default_ext
    ofn.Flags       = 0x00000002  # OFN_OVERWRITEPROMPT
    if ctypes.windll.comdlg32.GetSaveFileNameW(ctypes.byref(ofn)):
        return buf.value
    return None


# ── Análise única ─────────────────────────────────────────────────────
def _open_file(slot: str):
    def _pick():
        try:
            path = _win_open_file(f"Selecionar faixa {slot}")
            if path:
                _ui(dpg.set_value,     W[f"file_{slot}"],        os.path.basename(path))
                _ui(dpg.set_value,     W[f"status_{slot}"],      "Analisando...")
                _ui(dpg.configure_item, W[f"btn_vis_{slot}"],    enabled=False)
                _ui(dpg.configure_item, W[f"btn_exp_csv_{slot}"], enabled=False)
                _ui(dpg.configure_item, W[f"btn_exp_json_{slot}"], enabled=False)
                threading.Thread(target=_analyze, args=(slot, path), daemon=True).start()
        except Exception as e:
            import traceback; traceback.print_exc()
            _ui(dpg.set_value, W[f"status_{slot}"], f"Erro: {e}")
    threading.Thread(target=_pick, daemon=True).start()


def _analyze(slot: str, path: str):
    try:
        f = analyze_file(path)
        c = classify(f)
        _tracks[slot] = {"features": f, "classification": c, "path": path}
        _ui(_apply_results, slot, f, c)
        _ui(_apply_spectrum, slot, f)
        _ui(_check_compare_ready)
        _ui(dpg.configure_item, W[f"btn_vis_{slot}"],     enabled=True)
        _ui(dpg.configure_item, W[f"btn_exp_csv_{slot}"], enabled=True)
        _ui(dpg.configure_item, W[f"btn_exp_json_{slot}"], enabled=True)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        _ui(dpg.set_value, W[f"status_{slot}"], f"Erro: {str(e)[:120]}")


def _apply_results(slot: str, f: dict, c: dict):
    dpg.set_value(W[f"status_{slot}"], "Concluído ✓")
    dpg.set_value(W[f"bpm_{slot}"],    f"{f['bpm']:.1f}")
    dpg.set_value(W[f"tom_{slot}"],    f["dominant_key"])
    dpg.set_value(W[f"dur_{slot}"],    _fmt_dur(f["duration_seconds"]))

    # Análise local
    rule = (c.get("rule_based_candidates") or c.get("candidates") or [{}])[0]
    dpg.set_value(W[f"genero_{slot}"], rule.get("genre", "—"))
    dpg.set_value(W[f"sub_{slot}"],    rule.get("subgenre") or "—")
    dpg.set_value(W[f"conf_{slot}"],   f"{rule.get('score', 0):.0%}")

    candidates = c.get("rule_based_candidates") or c.get("candidates", [])
    for i in range(3):
        if i < len(candidates):
            cand = candidates[i]
            txt = (f"  {i+1}. {cand['genre']} / {cand.get('subgenre','—')}"
                   f"  —  {cand['score']:.0%}"
                   f"  ({cand.get('bpm_range','—')} BPM)")
        else:
            txt = ""
        dpg.set_value(W[f"cand_{slot}_{i}"], txt)

    # Spotify
    ext      = c.get("lastfm")
    sp_feats = (ext or {}).get("spotify_features") or {}

    if ext and ext.get("method") == "spotify":
        dpg.set_value(W[f"sp_genre_{slot}"], ext.get("genre", "—"))
        dpg.set_value(W[f"sp_sub_{slot}"],   ext.get("subgenre") or "—")
        dpg.set_value(W[f"sp_tags_{slot}"],  ", ".join(ext.get("top_tags", [])) or "—")
    else:
        dpg.set_value(W[f"sp_genre_{slot}"], "—")
        dpg.set_value(W[f"sp_sub_{slot}"],   "—")
        dpg.set_value(W[f"sp_tags_{slot}"],  "—")

    if sp_feats:
        parts = []
        if sp_feats.get("tempo"):                   parts.append(f"BPM {sp_feats['tempo']:.0f}")
        if sp_feats.get("energy")       is not None: parts.append(f"energy {sp_feats['energy']:.0%}")
        if sp_feats.get("danceability") is not None: parts.append(f"dance {sp_feats['danceability']:.0%}")
        if sp_feats.get("valence")      is not None: parts.append(f"valence {sp_feats['valence']:.0%}")
        dpg.set_value(W[f"sp_feats_{slot}"], "  ".join(parts) or "—")
    else:
        dpg.set_value(W[f"sp_feats_{slot}"], "—")

    # Last.fm
    if ext and ext.get("method") == "lastfm":
        dpg.set_value(W[f"lfm_genre_{slot}"], ext.get("genre", "—"))
        dpg.set_value(W[f"lfm_sub_{slot}"],   ext.get("subgenre") or "—")
        dpg.set_value(W[f"lfm_tags_{slot}"],  ", ".join(ext.get("top_tags", [])) or "—")
    else:
        dpg.set_value(W[f"lfm_genre_{slot}"], "—")
        dpg.set_value(W[f"lfm_sub_{slot}"],   "—")
        dpg.set_value(W[f"lfm_tags_{slot}"],  "sem metadados / não encontrado")


def _apply_spectrum(slot: str, f: dict):
    e_sub  = f.get("energy_sub_bass", 0)
    e_bass = f.get("energy_bass", 0)
    e_mid  = f.get("energy_mid", 0)
    e_high = f.get("energy_high", 0)
    vals   = [e_sub, e_bass, e_mid*0.4, e_mid*0.6, e_high*0.6, e_high*0.4]
    labels = ["Sub-bass 20–80Hz", "Bass 80–300Hz", "Low-mid 300–1kHz",
              "Mid 1–3kHz", "High-mid 3–8kHz", "High 8–16kHz"]
    max_val = max(vals) or 1.0

    for i, (val, label) in enumerate(zip(vals, labels)):
        ratio = val / max_val
        dpg.set_value(W[f"bar_{slot}_{i}"],    ratio)
        dpg.set_value(W[f"barlbl_{slot}_{i}"], f"{label}  {ratio:.0%}")

    envelope = f.get("rms_envelope")
    if envelope:
        samples = [float(v) for v in envelope]
    else:
        rng = np.random.default_rng(seed=42)
        samples = [float(v) for v in np.clip(
            rng.normal(f.get("rms_mean", 0.01), f.get("rms_std", 0.005) * 0.5, 80), 0, None
        )]
    dpg.set_value(W[f"wave_{slot}"], [list(range(len(samples))), samples])


def _check_compare_ready():
    ready = _tracks["A"] is not None and _tracks["B"] is not None
    dpg.configure_item(W["btn_compare"], enabled=ready)


# ── Exportar análise única ────────────────────────────────────────────
def _export_single(slot: str, fmt: str):
    def _do():
        track = _tracks.get(slot)
        if not track:
            return
        f, c = track["features"], track["classification"]
        rule = (c.get("rule_based_candidates") or c.get("candidates") or [{}])[0]
        ext  = c.get("lastfm") or {}

        path = _win_save_file(
            title=f"Exportar como {fmt.upper()}",
            filter_str=f"{fmt.upper()}\0*.{fmt}\0",
            default_ext=fmt,
        )
        if not path:
            return

        row = {
            "file":              f.get("file_name"),
            "bpm":               round(f.get("bpm", 0), 1),
            "key":               f.get("dominant_key"),
            "duration":          round(f.get("duration_seconds", 0), 1),
            "genre_local":       rule.get("genre"),
            "subgenre_local":    rule.get("subgenre"),
            "confidence_local":  round(rule.get("score", 0), 3),
            "genre_external":    ext.get("genre"),
            "subgenre_external": ext.get("subgenre"),
            "source_external":   ext.get("method"),
            "bass_ratio":        round(f.get("bass_ratio", 0), 3),
            "rms_mean":          round(f.get("rms_mean", 0), 4),
        }
        try:
            if fmt == "json":
                with open(path, "w", encoding="utf-8") as fp:
                    json.dump(row, fp, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", newline="", encoding="utf-8") as fp:
                    writer = csv.DictWriter(fp, fieldnames=list(row.keys()))
                    writer.writeheader()
                    writer.writerow(row)
            _ui(dpg.set_value, W[f"status_{slot}"], f"Exportado: {os.path.basename(path)}")
        except Exception as e:
            _ui(dpg.set_value, W[f"status_{slot}"], f"Erro ao exportar: {e}")

    threading.Thread(target=_do, daemon=True).start()


# ── Visualizador matplotlib ───────────────────────────────────────────
def _open_visualizer(slot: str):
    track = _tracks.get(slot)
    if not track:
        return
    def _show():
        try:
            from visualizer import plot_analysis
            plot_analysis(track["path"], track["features"], track["classification"])
        except Exception as e:
            import traceback; traceback.print_exc()
            _ui(dpg.set_value, W[f"status_{slot}"], f"Visualizador: {e}")
    threading.Thread(target=_show, daemon=True).start()


def _open_vis_from_batch(track: dict):
    def _show():
        try:
            from visualizer import plot_analysis
            plot_analysis(track["path"], track["features"], track["classification"])
        except Exception as e:
            import traceback; traceback.print_exc()
    threading.Thread(target=_show, daemon=True).start()


# ── Comparar ──────────────────────────────────────────────────────────
def _run_compare():
    a = _tracks.get("A")
    b = _tracks.get("B")
    if not (a and b):
        return
    fa, ca = a["features"], a["classification"]
    fb, cb = b["features"], b["classification"]

    def diff(v1, v2, fmt=".1f", unit=""):
        d = v2 - v1
        return f"{'+'if d>0 else ''}{d:{fmt}}{unit}"

    def _local_conf(c):
        rule = (c.get("rule_based_candidates") or c.get("candidates") or [{}])[0]
        return f"{rule.get('score', 0):.0%} (local)"

    rows = [
        ("BPM",         f"{fa['bpm']:.1f}",                     f"{fb['bpm']:.1f}",
                        diff(fa["bpm"], fb["bpm"])),
        ("Tom",         fa["dominant_key"],                      fb["dominant_key"],          "—"),
        ("Duração",     _fmt_dur(fa["duration_seconds"]),        _fmt_dur(fb["duration_seconds"]), "—"),
        ("Gênero",      ca["genre"],                             cb["genre"],                 "—"),
        ("Subgênero",   ca.get("subgenre") or "—",               cb.get("subgenre") or "—",   "—"),
        ("Confiança",   _local_conf(ca),                         _local_conf(cb),             "—"),
        ("Graves",      f"{fa['bass_ratio']:.0%}",               f"{fb['bass_ratio']:.0%}",
                        diff(fa["bass_ratio"], fb["bass_ratio"], ".1%")),
        ("Brilho",      f"{fa['spectral_centroid_mean']:.0f}Hz", f"{fb['spectral_centroid_mean']:.0f}Hz",
                        diff(fa["spectral_centroid_mean"], fb["spectral_centroid_mean"], ".0f", "Hz")),
        ("Energia RMS", f"{fa['rms_mean']:.4f}",                 f"{fb['rms_mean']:.4f}",
                        diff(fa["rms_mean"], fb["rms_mean"], ".4f")),
        ("Percussão",   f"{fa['percussive_ratio']:.2f}",         f"{fb['percussive_ratio']:.2f}",
                        diff(fa["percussive_ratio"], fb["percussive_ratio"], ".2f")),
    ]
    for i, (label, v1, v2, d) in enumerate(rows):
        dpg.set_value(W[f"cmp_lbl_{i}"], label)
        dpg.set_value(W[f"cmp_a_{i}"],   v1)
        dpg.set_value(W[f"cmp_b_{i}"],   v2)
        dpg.set_value(W[f"cmp_d_{i}"],   d)

    dpg.set_value(W["compare_titles"],
                  f"{fa['file_name'][:28]}  vs  {fb['file_name'][:28]}")


# ── Análise em lote ───────────────────────────────────────────────────
def _open_batch_folder():
    def _pick():
        try:
            folder = _win_open_folder("Selecionar pasta com músicas")
            if folder:
                _ui(dpg.set_value,      W["batch_folder_text"], folder)
                _ui(dpg.set_value,      W["batch_status"],      "Iniciando...")
                _ui(dpg.set_value,      W["batch_progress"],    0.0)
                _ui(dpg.configure_item, W["batch_exp_csv"],     enabled=False)
                _ui(dpg.configure_item, W["batch_exp_json"],    enabled=False)
                threading.Thread(target=_run_batch, args=(folder,), daemon=True).start()
        except Exception as e:
            import traceback; traceback.print_exc()
            _ui(dpg.set_value, W["batch_status"], f"Erro: {e}")
    threading.Thread(target=_pick, daemon=True).start()


def _run_batch(folder: str):
    global _batch_results, _batch_folder
    _batch_folder = folder

    supported = set(SUPPORTED_FORMATS)
    files = sorted([
        os.path.join(folder, fn)
        for fn in os.listdir(folder)
        if os.path.splitext(fn)[1].lower() in supported
    ])

    if not files:
        _ui(dpg.set_value, W["batch_status"], "Nenhum arquivo de áudio encontrado.")
        return

    total   = len(files)
    results = []
    errors  = []

    for i, path in enumerate(files):
        _ui(dpg.set_value, W["batch_status"],
            f"{i+1}/{total} — {os.path.basename(path)[:50]}")
        _ui(dpg.set_value, W["batch_progress"], (i + 1) / total)
        try:
            f = analyze_file(path)
            c = classify(f)
            results.append({"features": f, "classification": c, "path": path})
        except Exception as e:
            errors.append((os.path.basename(path), str(e)))

    _batch_results = results
    _ui(_apply_batch_table, results, errors)
    _ui(dpg.configure_item, W["batch_exp_csv"],  enabled=bool(results))
    _ui(dpg.configure_item, W["batch_exp_json"], enabled=bool(results))
    err_txt = f"  ({len(errors)} erro(s))" if errors else ""
    _ui(dpg.set_value, W["batch_status"],
        f"Concluído — {len(results)} faixas analisadas{err_txt}")


def _apply_batch_table(results: list[dict], errors: list):
    # Remove tabela anterior se existir
    prev = W.get("batch_table")
    if prev:
        try:
            dpg.delete_item(prev)
        except Exception:
            pass

    container = W["batch_table_container"]
    tbl = dpg.add_table(
        parent=container,
        header_row=True,
        borders_innerH=True,
        borders_outerH=True,
        borders_outerV=True,
        row_background=True,
    )
    W["batch_table"] = tbl

    for col in ["Arquivo", "BPM", "Tom", "Gênero", "Subgênero", "Confiança", "Fonte", ""]:
        dpg.add_table_column(parent=tbl, label=col)

    for r in results:
        f    = r["features"]
        c    = r["classification"]
        rule = (c.get("rule_based_candidates") or c.get("candidates") or [{}])[0]
        ext  = c.get("lastfm") or {}
        row  = dpg.add_table_row(parent=tbl)
        dpg.add_text(os.path.basename(f.get("file_path", ""))[:42], parent=row)
        dpg.add_text(f"{f['bpm']:.1f}",                  parent=row, color=list(YELLOW))
        dpg.add_text(f.get("dominant_key", "?"),          parent=row, color=list(PURPLE))
        dpg.add_text(c.get("genre", "—"),                 parent=row, color=list(GREEN))
        dpg.add_text(c.get("subgenre") or "—",            parent=row)
        dpg.add_text(f"{rule.get('score', 0):.0%}",       parent=row, color=list(ACCENT))
        dpg.add_text(ext.get("method", "local").upper(),  parent=row, color=list(DIM))
        td = {"path": r["path"], "features": f, "classification": c}
        dpg.add_button(label="Vis.", callback=lambda _, __, td=td: _open_vis_from_batch(td), parent=row)

    for name, err in errors:
        row = dpg.add_table_row(parent=tbl)
        dpg.add_text(f"[ERRO] {name[:42]}", parent=row, color=list(RED))
        dpg.add_text(err[:20], parent=row, color=list(DIM))
        for _ in range(6):
            dpg.add_text("", parent=row)


def _export_batch(fmt: str):
    def _do():
        if not _batch_results:
            return
        path = _win_save_file(
            title=f"Exportar lote como {fmt.upper()}",
            filter_str=f"{fmt.upper()}\0*.{fmt}\0",
            default_ext=fmt,
        )
        if not path:
            return

        rows = []
        for r in _batch_results:
            f    = r["features"]
            c    = r["classification"]
            rule = (c.get("rule_based_candidates") or c.get("candidates") or [{}])[0]
            ext  = c.get("lastfm") or {}
            rows.append({
                "file":              f.get("file_name"),
                "bpm":               round(f.get("bpm", 0), 1),
                "key":               f.get("dominant_key"),
                "duration":          round(f.get("duration_seconds", 0), 1),
                "genre_local":       rule.get("genre"),
                "subgenre_local":    rule.get("subgenre"),
                "confidence_local":  round(rule.get("score", 0), 3),
                "genre_external":    ext.get("genre"),
                "subgenre_external": ext.get("subgenre"),
                "source_external":   ext.get("method"),
                "bass_ratio":        round(f.get("bass_ratio", 0), 3),
                "rms_mean":          round(f.get("rms_mean", 0), 4),
            })

        try:
            if fmt == "json":
                with open(path, "w", encoding="utf-8") as fp:
                    json.dump(rows, fp, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", newline="", encoding="utf-8") as fp:
                    writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            _ui(dpg.set_value, W["batch_status"],
                f"Exportado: {os.path.basename(path)}")
        except Exception as e:
            _ui(dpg.set_value, W["batch_status"], f"Erro ao exportar: {e}")

    threading.Thread(target=_do, daemon=True).start()


# ── Utilitário de thumbnail ───────────────────────────────────────────
_cover_tex_counter = 0

def _register_cover_texture(data: bytes, size: int = 48) -> str | None:
    """Converte bytes de imagem em texture DearPyGui. Retorna tag ou None."""
    global _cover_tex_counter
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
        flat = [v / 255.0 for px in img.getdata() for v in px]
        tag = f"__cov_{_cover_tex_counter}__"
        _cover_tex_counter += 1
        dpg.add_static_texture(size, size, flat, tag=tag, parent="__covers__")
        return tag
    except Exception:
        return None


# ── Auto-tagging ──────────────────────────────────────────────────────
def _open_tag_target():
    def _pick():
        try:
            path = _win_open_file("Selecionar arquivo para tagar")
            if path:
                _ui(dpg.set_value, W["tag_target_text"], path)
                _ui(dpg.configure_item, W["tag_run_btn"], enabled=True)
        except Exception as e:
            _ui(dpg.set_value, W["tag_status"], f"Erro: {e}")
    threading.Thread(target=_pick, daemon=True).start()


def _open_tag_folder():
    def _pick():
        try:
            folder = _win_open_folder("Selecionar pasta para tagar")
            if folder:
                _ui(dpg.set_value, W["tag_target_text"], folder)
                _ui(dpg.configure_item, W["tag_run_btn"], enabled=True)
        except Exception as e:
            _ui(dpg.set_value, W["tag_status"], f"Erro: {e}")
    threading.Thread(target=_pick, daemon=True).start()


def _run_tag():
    target    = dpg.get_value(W["tag_target_text"])
    dry_run     = dpg.get_value(W["tag_dry_run"])
    apply_year  = dpg.get_value(W["tag_apply_year"])
    apply_cover = dpg.get_value(W["tag_apply_cover"])
    no_genre    = dpg.get_value(W["tag_no_genre"])

    if not target:
        return

    _ui(dpg.set_value,      W["tag_status"],   "Processando...")
    _ui(dpg.set_value,      W["tag_progress"], 0.0)
    _ui(dpg.configure_item, W["tag_run_btn"],  enabled=False)

    def _do():
        try:
            fetch_year  = apply_year
            fetch_cover = apply_cover
            fetch_genre = not no_genre
            if os.path.isfile(target):
                files = [target]
            else:
                files = [
                    os.path.join(target, fn)
                    for fn in sorted(os.listdir(target))
                    if os.path.splitext(fn)[1].lower() in set(SUPPORTED_FORMATS)
                ]

            total   = len(files)
            results = []
            for i, fpath in enumerate(files):
                _ui(dpg.set_value, W["tag_status"],
                    f"{i+1}/{total} — {os.path.basename(fpath)[:50]}")
                _ui(dpg.set_value, W["tag_progress"],
                    (i + 1) / total if total else 1.0)
                results.append(tag_file(
                    fpath, dry_run=dry_run,
                    fetch_year_online=fetch_year, fetch_cover=fetch_cover,
                    fetch_genre=fetch_genre,
                ))

            _ui(_apply_tag_table, results, dry_run)
        except Exception as e:
            import traceback; traceback.print_exc()
            _ui(dpg.set_value, W["tag_status"], f"Erro: {e}")
        finally:
            _ui(dpg.configure_item, W["tag_run_btn"], enabled=True)

    threading.Thread(target=_do, daemon=True).start()


def _apply_tag_table(results: list[dict], dry_run: bool):
    prev = W.get("tag_table")
    if prev:
        try:
            dpg.delete_item(prev)
        except Exception:
            pass

    ok  = sum(1 for r in results if r.get("written"))
    err = sum(1 for r in results if r.get("error") and not r.get("written"))
    mode = " (dry-run)" if dry_run else ""
    dpg.set_value(W["tag_status"],
                  f"Concluído{mode} — {ok} tagado(s)  {err} erro(s)")

    container = W["tag_table_container"]
    tbl = dpg.add_table(
        parent=container,
        header_row=True,
        borders_innerH=True,
        borders_outerH=True,
        borders_outerV=True,
        row_background=True,
    )
    W["tag_table"] = tbl

    for col in ["Arquivo", "Artista", "Título", "Gênero", "Ano", "Capa", "Status"]:
        dpg.add_table_column(parent=tbl, label=col)

    for r in results:
        row = dpg.add_table_row(parent=tbl)
        dpg.add_text((r.get("file") or "")[:38],  parent=row)
        dpg.add_text(r.get("artist") or "—",     parent=row, color=list(ACCENT))
        dpg.add_text(r.get("title") or "—",      parent=row, color=list(ACCENT2))
        dpg.add_text(r.get("genre") or "—",      parent=row, color=list(GREEN))
        dpg.add_text(r.get("year") or "—",       parent=row, color=list(YELLOW))

        cover_data = r.get("cover_preview")
        if cover_data:
            tex = _register_cover_texture(cover_data)
            if tex:
                dpg.add_image(tex, parent=row, width=48, height=48)
            else:
                dpg.add_text("✓" if r.get("cover_written") else "img?", parent=row, color=list(GREEN))
        elif r.get("cover_written"):
            dpg.add_text("✓", parent=row, color=list(GREEN))
        else:
            dpg.add_text("—", parent=row, color=list(DIM))

        if r.get("error") and not r.get("written"):
            dpg.add_text(r["error"][:30], parent=row, color=list(RED))
        elif r.get("written"):
            label = "preview" if dry_run else "✓"
            dpg.add_text(label, parent=row, color=list(YELLOW if dry_run else GREEN))
        else:
            dpg.add_text("—", parent=row, color=list(DIM))


# ── Construtores UI ───────────────────────────────────────────────────
def _build_info_panel(slot: str, label: str, color):
    dpg.add_text(label, color=list(color))
    W[f"file_{slot}"]   = dpg.add_text("Nenhum arquivo", color=list(DIM), wrap=290)
    W[f"status_{slot}"] = dpg.add_text("")
    dpg.add_button(label="  Abrir arquivo  ", callback=lambda *_: _open_file(slot))
    dpg.add_separator()

    for key, wkey in [("BPM", "bpm"), ("Tom", "tom"), ("Duração", "dur")]:
        with dpg.group(horizontal=True):
            dpg.add_text(f"{key}:", color=list(DIM))
            W[f"{wkey}_{slot}"] = dpg.add_text("—")

    # Análise local
    dpg.add_spacer(height=8)
    dpg.add_text("Análise local", color=list(ACCENT))
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text("Gênero:",    color=list(DIM))
        W[f"genero_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Subgênero:", color=list(DIM))
        W[f"sub_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Confiança:", color=list(DIM))
        W[f"conf_{slot}"] = dpg.add_text("—")
    dpg.add_text("Top candidatos:", color=list(DIM))
    for i in range(3):
        W[f"cand_{slot}_{i}"] = dpg.add_text("", color=list(WHITE), wrap=290)

    # Spotify
    dpg.add_spacer(height=8)
    dpg.add_text("Spotify", color=list(SP_GREEN))
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text("Gênero:",    color=list(DIM))
        W[f"sp_genre_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Subgênero:", color=list(DIM))
        W[f"sp_sub_{slot}"] = dpg.add_text("—")
    W[f"sp_tags_{slot}"]  = dpg.add_text("—", color=list(DIM), wrap=290)
    W[f"sp_feats_{slot}"] = dpg.add_text("—", color=list(DIM), wrap=290)

    # Last.fm
    dpg.add_spacer(height=8)
    dpg.add_text("Last.fm", color=list(LFM_RED))
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text("Gênero:",    color=list(DIM))
        W[f"lfm_genre_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Subgênero:", color=list(DIM))
        W[f"lfm_sub_{slot}"] = dpg.add_text("—")
    W[f"lfm_tags_{slot}"] = dpg.add_text("—", color=list(DIM), wrap=290)

    # Ações
    dpg.add_spacer(height=10)
    dpg.add_separator()
    dpg.add_spacer(height=4)
    with dpg.group(horizontal=True):
        W[f"btn_vis_{slot}"] = dpg.add_button(
            label=" Visualizar ",
            callback=lambda *_: _open_visualizer(slot),
            enabled=False,
        )
        W[f"btn_exp_csv_{slot}"] = dpg.add_button(
            label=" CSV ",
            callback=lambda *_: _export_single(slot, "csv"),
            enabled=False,
        )
        W[f"btn_exp_json_{slot}"] = dpg.add_button(
            label=" JSON ",
            callback=lambda *_: _export_single(slot, "json"),
            enabled=False,
        )


def _build_spectrum(slot: str, color):
    dpg.add_text("Espectro de Frequências", color=list(color))
    dpg.add_separator()
    for i, bar_color in enumerate(_SPEC_COLORS):
        W[f"barlbl_{slot}_{i}"] = dpg.add_text("—", color=list(bar_color))
        pb = dpg.add_progress_bar(default_value=0.0, width=-1)
        with dpg.theme() as _t:
            with dpg.theme_component(dpg.mvProgressBar):
                dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, bar_color)
        dpg.bind_item_theme(pb, _t)
        W[f"bar_{slot}_{i}"] = pb
        dpg.add_spacer(height=2)

    dpg.add_spacer(height=8)
    dpg.add_text("Waveform", color=list(DIM))
    with dpg.plot(height=90, width=-1, no_title=True, no_mouse_pos=True) as _plot:
        dpg.add_plot_axis(dpg.mvXAxis,
                          no_gridlines=True, no_tick_marks=True, no_tick_labels=True)
        with dpg.plot_axis(dpg.mvYAxis,
                           no_gridlines=True, no_tick_marks=True, no_tick_labels=True):
            W[f"wave_{slot}"] = dpg.add_line_series([], [])
    with dpg.theme() as _pt:
        with dpg.theme_component(dpg.mvLineSeries):
            dpg.add_theme_color(dpg.mvPlotCol_Line, ACCENT, category=dpg.mvThemeCat_Plots)
    dpg.bind_item_theme(_plot, _pt)


def _build_compare_table():
    with dpg.table(header_row=True, borders_innerH=True, borders_outerH=True,
                   borders_outerV=True, row_background=True, width=-1):
        dpg.add_table_column(label="Atributo")
        dpg.add_table_column(label="Faixa A")
        dpg.add_table_column(label="Faixa B")
        dpg.add_table_column(label="Diferença")
        for i in range(10):
            with dpg.table_row():
                W[f"cmp_lbl_{i}"] = dpg.add_text("")
                W[f"cmp_a_{i}"]   = dpg.add_text("", color=list(ACCENT))
                W[f"cmp_b_{i}"]   = dpg.add_text("", color=list(ACCENT2))
                W[f"cmp_d_{i}"]   = dpg.add_text("", color=list(WHITE))


def _build_tag_tab():
    dpg.add_text("Auto-tagging", color=list(ACCENT))
    dpg.add_separator()
    dpg.add_spacer(height=6)
    dpg.add_text(
        "Lê o nome do arquivo (\"Artista - Título.ext\") e grava as tags ID3/Vorbis.",
        color=list(DIM), wrap=700,
    )
    dpg.add_spacer(height=8)

    with dpg.group(horizontal=True):
        dpg.add_button(label="  Arquivo  ", callback=lambda *_: _open_tag_target())
        dpg.add_button(label="  Pasta  ",   callback=lambda *_: _open_tag_folder())
        W["tag_target_text"] = dpg.add_text("Nenhum alvo selecionado", color=list(DIM))

    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        W["tag_dry_run"]    = dpg.add_checkbox(label="Dry-run (não grava)")
        dpg.add_spacer(width=16)
        W["tag_apply_year"] = dpg.add_checkbox(label="Aplicar ano", default_value=True)
        dpg.add_spacer(width=16)
        W["tag_apply_cover"] = dpg.add_checkbox(label="Aplicar capa", default_value=True)
        dpg.add_spacer(width=16)
        W["tag_no_genre"]   = dpg.add_checkbox(label="Não buscar gênero")

    dpg.add_spacer(height=6)
    W["tag_run_btn"] = dpg.add_button(
        label="  Tagar  ",
        callback=lambda *_: _run_tag(),
        enabled=False,
    )

    dpg.add_spacer(height=8)
    W["tag_status"]   = dpg.add_text("", color=list(DIM))
    W["tag_progress"] = dpg.add_progress_bar(default_value=0.0, width=-1)
    dpg.add_spacer(height=6)
    W["tag_table_container"] = dpg.add_group()


def _build_batch_tab():
    dpg.add_text("Análise em Lote", color=list(ACCENT))
    dpg.add_separator()
    dpg.add_spacer(height=6)

    with dpg.group(horizontal=True):
        dpg.add_button(label="  Selecionar pasta  ",
                       callback=lambda *_: _open_batch_folder())
        W["batch_folder_text"] = dpg.add_text("Nenhuma pasta selecionada",
                                               color=list(DIM))

    dpg.add_spacer(height=8)
    W["batch_status"]   = dpg.add_text("", color=list(DIM))
    W["batch_progress"] = dpg.add_progress_bar(default_value=0.0, width=-1)

    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        W["batch_exp_csv"]  = dpg.add_button(
            label=" Exportar CSV ",
            callback=lambda *_: _export_batch("csv"),
            enabled=False,
        )
        W["batch_exp_json"] = dpg.add_button(
            label=" Exportar JSON ",
            callback=lambda *_: _export_batch("json"),
            enabled=False,
        )

    dpg.add_spacer(height=10)
    W["batch_table_container"] = dpg.add_group()


# ── Launch ────────────────────────────────────────────────────────────
def launch():
    dpg.create_context()
    dpg.add_texture_registry(tag="__covers__")

    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            # Fundos progressivamente mais claros com toque roxo
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,           ( 10,   8,  16, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,            ( 18,  14,  28, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,            ( 30,  22,  44, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,     ( 42,  30,  62, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,      ( 55,  38,  80, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,            ( 18,  14,  28, 240))
            # Botões: roxo escuro → verde no hover/active
            dpg.add_theme_color(dpg.mvThemeCol_Button,             ( 45,  28,  72, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,      ( 20, 160,  55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,       ( 50, 255,  80, 255))
            # Texto branco-roxo
            dpg.add_theme_color(dpg.mvThemeCol_Text,               (215, 210, 238, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled,       ( 90,  80, 115, 255))
            # Títulos de janela
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,            ( 14,  10,  24, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,      ( 28,  60,  28, 255))
            # Abas: roxo escuro → verde neon na ativa
            dpg.add_theme_color(dpg.mvThemeCol_Tab,                ( 22,  16,  38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered,         ( 25, 140,  50, 200))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive,          ( 35, 200,  60, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabUnfocused,       ( 16,  12,  28, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabUnfocusedActive, ( 28,  80,  40, 255))
            # Separadores e bordas em roxo suave
            dpg.add_theme_color(dpg.mvThemeCol_Separator,          ( 65,  42, 100, 255))
            dpg.add_theme_color(dpg.mvThemeCol_SeparatorHovered,   (120,  70, 175, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Border,             ( 55,  38,  85, 180))
            # Checkbox / radio: verde neon
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark,          ( 50, 255,  80, 255))
            # Barra de progresso: verde neon
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,      ( 50, 255,  80, 255))
            # Scrollbar em roxo
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,        ( 12,  10,  20, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,      ( 80,  48, 120, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered,(120,  70, 175, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive, (170,  40, 255, 255))
            # Cabeçalho de tabela
            dpg.add_theme_color(dpg.mvThemeCol_Header,             ( 50, 255,  80,  45))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,      ( 50, 255,  80,  80))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,       ( 50, 255,  80, 120))
            # Estilos
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,  8)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,   5)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,    5)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding,     5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,     8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,  12, 12)
    dpg.bind_theme(global_theme)

    with dpg.window(tag="main_window", no_title_bar=True, no_move=True, no_resize=True):
        dpg.add_text("Music Analyzer", color=list(ACCENT))
        dpg.add_separator()

        with dpg.tab_bar():

            # ── Analisar ────────────────────────────────────────────
            with dpg.tab(label="  Analisar  "):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=315, border=True):
                        _build_info_panel("A", "Faixa A", ACCENT)
                    with dpg.child_window(border=True):
                        _build_spectrum("A", ACCENT)

            # ── Comparar ────────────────────────────────────────────
            with dpg.tab(label="  Comparar  "):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=315, border=True):
                        _build_info_panel("B", "Faixa B", ACCENT2)
                        dpg.add_separator()
                        _build_spectrum("B", ACCENT2)

                    with dpg.child_window(border=True):
                        dpg.add_text("Comparação de Faixas", color=list(ACCENT))
                        dpg.add_text(
                            "Carregue Faixa A na aba Analisar e Faixa B aqui.",
                            color=list(DIM),
                        )
                        W["compare_titles"] = dpg.add_text("", color=list(DIM))
                        dpg.add_spacer(height=4)
                        W["btn_compare"] = dpg.add_button(
                            label="  Comparar agora  ",
                            callback=lambda *_: _run_compare(),
                            enabled=False,
                        )
                        dpg.add_separator()
                        _build_compare_table()

            # ── Lote ────────────────────────────────────────────────
            with dpg.tab(label="  Lote  "):
                with dpg.child_window(border=False):
                    _build_batch_tab()

            # ── Tagar ───────────────────────────────────────────────
            with dpg.tab(label="  Tagar  "):
                with dpg.child_window(border=False):
                    _build_tag_tab()

    dpg.create_viewport(title="Music Analyzer", width=1280, height=800,
                        min_width=960, min_height=620)
    dpg.setup_dearpygui()
    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()

    while dpg.is_dearpygui_running():
        try:
            _process_ui_queue()
        except Exception:
            import traceback; traceback.print_exc()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    launch()
