"""
Music Analyzer — Interface Gráfica (DearPyGui 2.x)
Uso: python gui.py  ou  python main.py --gui
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import threading
import queue
import ctypes
from ctypes import wintypes
import traceback

import numpy as np
import dearpygui.dearpygui as dpg

from analyzer import analyze_file, dj_compatibility
from classifier import classify
from config import (SUPPORTED_FORMATS, APP_NAME, APP_VERSION,
                    COMPANY_NAME, APP_DESCRIPTION, APP_COPYRIGHT)
from tagger import tag_file

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
            traceback.print_exc()

# ── Estado global ─────────────────────────────────────────────────────
W: dict = {}
_tracks        = {"A": None, "B": None}
_batch_results: list[dict] = []
_batch_folder:  str | None = None
_dl_process:      "subprocess.Popen | None" = None
_dl_process_lock: threading.Lock            = threading.Lock()
_dl_dest_folder:  str                       = os.path.expanduser("~/Downloads")
_dl_log_lines:    list                      = []
_dl_log_lock:     threading.Lock            = threading.Lock()
_train_log_lines: list[str]                 = []
_train_log_lock:  threading.Lock            = threading.Lock()
_train_stop_flag: threading.Event          = threading.Event()
_audit_results:   list[dict]                = []
_dl_search_results: list                    = []

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

_WAVE_N = 512  # pontos da waveform (resolução do envelope)

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


def _win_open_file(title="Selecionar arquivo") -> str | None:
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
    try:
        pidl = browse(ctypes.byref(bi))
        if pidl:
            get_path(ctypes.c_void_p(pidl), path_buf)
            ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
            return path_buf.value or None
        return None
    finally:
        ctypes.windll.ole32.CoUninitialize()


def _win_save_file(title="Salvar", filter_str="CSV\0*.csv\0", default_ext="csv") -> str | None:
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


# ── Helper de exportação ──────────────────────────────────────────────
def _build_export_row(r: dict) -> dict:
    f    = r["features"]
    c    = r["classification"]
    rule = (c.get("rule_based_candidates") or c.get("candidates") or [{}])[0]
    ext  = c.get("lastfm") or {}
    return {
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
            traceback.print_exc()
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
        threading.Thread(target=_compute_waveform, args=(slot, path), daemon=True).start()
        _ui(dpg.configure_item, W[f"btn_vis_{slot}"],     enabled=True)
        _ui(dpg.configure_item, W[f"btn_exp_csv_{slot}"], enabled=True)
        _ui(dpg.configure_item, W[f"btn_exp_json_{slot}"], enabled=True)
    except Exception as e:
        print(traceback.format_exc())
        _ui(dpg.set_value, W[f"status_{slot}"], f"Erro: {str(e)[:120]}")


def _apply_results(slot: str, f: dict, c: dict):
    dpg.set_value(W[f"status_{slot}"], "Concluído OK")
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
    labels = ["Sub-bass 20-80Hz", "Bass 80-300Hz", "Low-mid 300-1kHz",
              "Mid 1-3kHz", "High-mid 3-8kHz", "High 8-16kHz"]
    max_val = max(vals) or 1.0

    for i, (val, label) in enumerate(zip(vals, labels)):
        ratio = val / max_val
        dpg.set_value(W[f"bar_{slot}_{i}"],    ratio)
        dpg.set_value(W[f"barlbl_{slot}_{i}"], f"{label}  {ratio:.0%}")

    # espectrograma calculado em thread separada por _compute_spectrogram


def _compute_waveform(slot: str, path: str):
    """Calcula envelope de pico (max/min) em background e atualiza a shade_series."""
    try:
        import librosa
        y, sr = librosa.load(path, sr=22050, mono=True)
        chunk = max(len(y) // _WAVE_N, 1)
        pos, neg = [], []
        for i in range(_WAVE_N):
            seg = y[i * chunk: (i + 1) * chunk]
            if len(seg) == 0:
                pos.append(0.0); neg.append(0.0)
            else:
                pos.append(float(seg.max()))
                neg.append(float(seg.min()))
        x = list(range(_WAVE_N))
        _ui(_update_waveform, slot, x, pos, neg)
    except Exception:
        traceback.print_exc()


def _update_waveform(slot: str, x: list, pos: list, neg: list):
    try:
        dpg.set_value(W[f"wave_{slot}"], [x, pos, neg])
    except Exception:
        pass


def _check_compare_ready():
    ready = _tracks["A"] is not None and _tracks["B"] is not None
    dpg.configure_item(W["btn_compare"], enabled=ready)


# ── Exportar análise única ────────────────────────────────────────────
def _export_single(slot: str, fmt: str):
    def _do():
        track = _tracks.get(slot)
        if not track:
            return

        path = _win_save_file(
            title=f"Exportar como {fmt.upper()}",
            filter_str=f"{fmt.upper()}\0*.{fmt}\0",
            default_ext=fmt,
        )
        if not path:
            return

        row = _build_export_row(track)
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
            traceback.print_exc()
            _ui(dpg.set_value, W[f"status_{slot}"], f"Visualizador: {e}")
    threading.Thread(target=_show, daemon=True).start()


def _open_vis_from_batch(track: dict):
    def _show():
        try:
            from visualizer import plot_analysis
            plot_analysis(track["path"], track["features"], track["classification"])
        except Exception as e:
            traceback.print_exc()
    threading.Thread(target=_show, daemon=True).start()


# ── Comparar ──────────────────────────────────────────────────────────

_COMPAT_COLORS = {
    'Perfeita':      (50,  255,  80, 255),   # verde neon
    'Boa':           (100, 255, 145, 255),   # verde suave
    'Possível':      (255, 200,  50, 255),   # amarelo
    'Difícil':       (255, 130,  50, 255),   # laranja
    'Incompatível':  (255,  70,  80, 255),   # vermelho
}


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
        ("Gênero",      ca.get("genre", "?"),                     cb.get("genre", "?"),        "—"),
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

    # ── Compatibilidade DJ ─────────────────────────────────────────
    compat = dj_compatibility(fa, fb, ca, cb)
    score  = compat['score']
    rating = compat['rating']
    color  = _COMPAT_COLORS.get(rating, list(WHITE))

    dpg.set_value(W["compat_score"], f"{score}%")
    dpg.configure_item(W["compat_score"], color=color)
    dpg.set_value(W["compat_rating"], rating)
    dpg.configure_item(W["compat_rating"], color=color)
    dpg.set_value(W["compat_bar"], score / 100)

    # Sub-scores
    dpg.set_value(W["compat_bpm"],    f"{compat['bpm']['score']}%")
    dpg.set_value(W["compat_key"],    f"{compat['key']['score']}%")
    dpg.set_value(W["compat_energy"], f"{compat['energy']['score']}%")
    dpg.set_value(W["compat_genre"],  f"{compat['genre']['score']}%")

    # Dicas
    tips_text = "\n".join(f"• {t}" for t in compat['tips'])
    dpg.set_value(W["compat_tips"], tips_text)


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
            traceback.print_exc()
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
        _ui(dpg.set_value, W["batch_progress"], i / total)
        try:
            f = analyze_file(path)
            c = classify(f)
            results.append({"features": f, "classification": c, "path": path})
        except Exception as e:
            errors.append((os.path.basename(path), str(e)))
        _ui(dpg.set_value, W["batch_progress"], (i + 1) / total)

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
        dpg.add_text(f"{f['bpm']:.1f}",                  parent=row, color=YELLOW)
        dpg.add_text(f.get("dominant_key", "?"),          parent=row, color=PURPLE)
        dpg.add_text(c.get("genre", "—"),                 parent=row, color=GREEN)
        dpg.add_text(c.get("subgenre") or "—",            parent=row)
        dpg.add_text(f"{rule.get('score', 0):.0%}",       parent=row, color=ACCENT)
        dpg.add_text(ext.get("method", "local").upper(),  parent=row, color=DIM)
        td = {"path": r["path"], "features": f, "classification": c}
        dpg.add_button(label="Vis.", callback=lambda _, __, td=td: _open_vis_from_batch(td), parent=row)

    for name, err in errors:
        row = dpg.add_table_row(parent=tbl)
        dpg.add_text(f"[ERRO] {name[:42]}", parent=row, color=RED)
        dpg.add_text(err[:20], parent=row, color=DIM)
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

        rows = [_build_export_row(r) for r in _batch_results]

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
_cover_texture_tags: list[str] = []

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
        _cover_texture_tags.append(tag)
        return tag
    except Exception:
        return None


# ── Download de músicas ───────────────────────────────────────────────
_YTDLP_EXE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def _dl_install_ytdlp():
    """Tenta pip install; se falhar, baixa o .exe diretamente do GitHub."""
    _ui(dpg.set_value,      W["dl_status"],      "Instalando yt-dlp via pip...")
    _ui(dpg.configure_item, W["dl_install_btn"], enabled=False)

    def _do():
        # ── Tentativa 1: pip ──────────────────────────────────────────
        pip_ok = False
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "yt-dlp"],
                capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=120,
            )
            pip_ok = r.returncode == 0
        except Exception:
            pass

        if pip_ok and _find_ytdlp():
            _ui(dpg.set_value,      W["dl_status"],     "yt-dlp instalado via pip! Pronto para usar.")
            _ui(dpg.configure_item, W["dl_ytdlp_warn"], show=False)
            _ui(dpg.configure_item, W["dl_install_btn"], enabled=True)
            return

        # ── Tentativa 2: baixar yt-dlp.exe diretamente ───────────────
        _ui(dpg.set_value, W["dl_status"],
            "pip falhou. Baixando yt-dlp.exe diretamente...")
        try:
            import urllib.request
            dest_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "yt-dlp.exe")
            urllib.request.urlretrieve(_YTDLP_EXE_URL, dest_exe)
            if os.path.isfile(dest_exe) and os.path.getsize(dest_exe) > 0:
                _ui(dpg.set_value,      W["dl_status"],     "yt-dlp.exe baixado! Pronto para usar.")
                _ui(dpg.configure_item, W["dl_ytdlp_warn"], show=False)
            else:
                raise RuntimeError("Arquivo baixado está vazio.")
        except Exception as e:
            _ui(dpg.set_value, W["dl_status"],
                f"Falha no download automático ({e}). "
                "Baixe yt-dlp.exe manualmente em github.com/yt-dlp/yt-dlp/releases "
                "e coloque na pasta do app.")
        finally:
            _ui(dpg.configure_item, W["dl_install_btn"], enabled=True)

    threading.Thread(target=_do, daemon=True).start()


def _run_search():
    global _dl_search_results
    query = dpg.get_value(W["dl_search_input"]).strip()
    if not query:
        return

    ytdlp = _find_ytdlp()
    if not ytdlp:
        _ui(dpg.set_value, W["dl_search_status"], "yt-dlp não encontrado.")
        return

    _ui(dpg.set_value,      W["dl_search_status"], "Pesquisando...")
    _ui(dpg.configure_item, W["dl_search_btn"],    enabled=False)

    def _do():
        global _dl_search_results
        proc = None
        try:
            proc = subprocess.Popen(
                [ytdlp, "--flat-playlist", "--dump-json", f"ytsearch10:{query}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            # Lê stdout em thread separada para não bloquear
            line_q: queue.Queue = queue.Queue()

            def _reader():
                try:
                    for line in proc.stdout:
                        line_q.put(line)
                finally:
                    line_q.put(None)

            threading.Thread(target=_reader, daemon=True).start()

            entries = []
            deadline = time.monotonic() + 90
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    break
                try:
                    line = line_q.get(timeout=min(remaining, 2.0))
                    if line is None:
                        break
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                except queue.Empty:
                    continue

            _dl_search_results = entries
            _ui(_apply_search_table, entries)
            _ui(dpg.set_value, W["dl_search_status"],
                f"{len(entries)} resultado(s)" if entries else "Nenhum resultado encontrado.")
        except Exception as e:
            traceback.print_exc()
            _ui(dpg.set_value, W["dl_search_status"], f"Erro: {e}")
        finally:
            if proc and proc.poll() is None:
                proc.kill()
            _ui(dpg.configure_item, W["dl_search_btn"], enabled=True)

    threading.Thread(target=_do, daemon=True).start()


def _apply_search_table(entries: list):
    prev = W.get("dl_search_table")
    if prev:
        try:
            dpg.delete_item(prev)
        except Exception:
            pass
    if not entries:
        return

    tbl = dpg.add_table(
        parent=W["dl_search_table_container"],
        header_row=True,
        borders_innerH=True,
        borders_outerH=True,
        borders_outerV=True,
        row_background=True,
    )
    W["dl_search_table"] = tbl

    for col in ["#", "Título", "Canal", "Duração", "Baixar"]:
        dpg.add_table_column(parent=tbl, label=col)

    for i, entry in enumerate(entries):
        vid_id   = entry.get("id") or ""
        title    = (entry.get("title") or "?")[:58]
        uploader = (entry.get("uploader") or entry.get("channel") or "—")[:26]
        dur      = entry.get("duration")
        dur_str  = _fmt_dur(float(dur)) if dur else "—"

        # url pode ser None, string vazia, só o ID, ou URL completa
        url_raw = entry.get("url") or entry.get("webpage_url") or ""
        if url_raw.startswith("http"):
            url = url_raw
        elif url_raw:          # yt-dlp devolveu só o ID no campo url
            url = f"https://www.youtube.com/watch?v={url_raw}"
        elif vid_id:
            url = f"https://www.youtube.com/watch?v={vid_id}"
        else:
            url = ""

        row = dpg.add_table_row(parent=tbl)
        dpg.add_text(str(i + 1), parent=row, color=DIM)
        dpg.add_text(title,      parent=row)
        dpg.add_text(uploader,   parent=row, color=DIM)
        dpg.add_text(dur_str,    parent=row, color=YELLOW)
        with dpg.group(horizontal=True, parent=row):
            dpg.add_button(
                label="MP3",
                callback=lambda s, a, u: _dl_from_result(u, audio=True),
                user_data=url,
                enabled=bool(url),
            )
            dpg.add_spacer(width=4)
            dpg.add_button(
                label="MP4",
                callback=lambda s, a, u: _dl_from_result(u, audio=False),
                user_data=url,
                enabled=bool(url),
            )


def _dl_from_result(url: str, audio: bool):
    """Baixa diretamente a partir de um resultado de busca."""
    if not url or not url.startswith("http"):
        dpg.set_value(W["dl_search_status"], "URL inválida neste resultado.")
        return
    # Preenche o campo e expande a seção para o usuário ver o progresso
    dpg.set_value(W["dl_url"],  url)
    dpg.set_value(W["dl_type"], "Áudio" if audio else "Vídeo")
    _dl_update_format_visibility()
    dpg.set_value(W["dl_url_section"], True)   # abre o painel "Baixar por URL"
    dpg.set_value(W["dl_search_status"], "Baixando...")
    _run_download(direct_url=url, direct_audio=audio)


def _find_ytdlp() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("yt-dlp.exe", "yt-dlp"):
        candidate = os.path.join(here, name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("yt-dlp")


def _dl_paste_url():
    try:
        ctypes.windll.user32.OpenClipboard(None)
        CF_UNICODETEXT = 13
        h = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
        text = ctypes.wstring_at(h) if h else ""
        ctypes.windll.user32.CloseClipboard()
        if text.strip():
            _ui(dpg.set_value, W["dl_url"], text.strip())
    except Exception:
        try:
            ctypes.windll.user32.CloseClipboard()
        except Exception:
            pass


def _dl_pick_folder():
    def _pick():
        global _dl_dest_folder
        folder = _win_open_folder("Pasta de destino para downloads")
        if folder:
            _dl_dest_folder = folder
            _ui(dpg.set_value, W["dl_dest_text"], folder)
    threading.Thread(target=_pick, daemon=True).start()


def _dl_update_format_visibility():
    is_audio = dpg.get_value(W["dl_type"]) == "Áudio"
    dpg.configure_item(W["dl_audio_grp"], show=is_audio)
    dpg.configure_item(W["dl_video_grp"], show=not is_audio)


def _dl_append_log(line: str):
    global _dl_log_lines
    with _dl_log_lock:
        _dl_log_lines.append(line)
        if len(_dl_log_lines) > 300:
            _dl_log_lines = _dl_log_lines[-300:]
        text = "\n".join(_dl_log_lines)
    _ui(dpg.set_value, W["dl_log"], text)


def _stop_download():
    with _dl_process_lock:
        proc = _dl_process
    if proc is not None and proc.poll() is None:
        proc.terminate()
        _ui(dpg.set_value,      W["dl_status"],   "Download cancelado.")
        _ui(dpg.configure_item, W["dl_run_btn"],  enabled=True)
        _ui(dpg.configure_item, W["dl_stop_btn"], enabled=False)


def _run_download(direct_url: str | None = None, direct_audio: bool | None = None):
    global _dl_process, _dl_log_lines

    # Aceita valores diretos (busca) ou lê dos widgets (botão Baixar)
    url = direct_url or dpg.get_value(W["dl_url"]).strip()
    if not url or not url.startswith("http"):
        _ui(dpg.set_value, W["dl_status"], "Insira uma URL válida (deve começar com http).")
        return

    ytdlp = _find_ytdlp()
    if not ytdlp:
        _ui(dpg.set_value, W["dl_status"],
            "yt-dlp não encontrado. Coloque yt-dlp.exe na pasta do projeto ou execute: pip install yt-dlp")
        return

    is_audio = direct_audio if direct_audio is not None else (dpg.get_value(W["dl_type"]) == "Áudio")
    playlist  = dpg.get_value(W["dl_playlist"])
    dest      = _dl_dest_folder
    pl_flag   = "--yes-playlist" if playlist else "--no-playlist"

    _AUDIO_QUAL = {
        "Melhor (320k)": "0",
        "Alta (256k)":   "5",
        "Média (192k)":  "7",
        "Baixa (128k)":  "9",
    }
    _VIDEO_FMT_SEL = {
        "Melhor": "bestvideo+bestaudio/best",
        "1080p":  "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "720p":   "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "480p":   "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "360p":   "bestvideo[height<=360]+bestaudio/best[height<=360]",
    }

    if is_audio:
        fmt  = dpg.get_value(W["dl_audio_fmt"])
        qual = _AUDIO_QUAL.get(dpg.get_value(W["dl_audio_quality"]), "0")
        cmd  = [ytdlp, "-x",
                "--audio-format", fmt,
                "--audio-quality", qual,
                "--newline", pl_flag,
                "-o", os.path.join(dest, "%(title)s.%(ext)s"),
                url]
    else:
        fmt     = dpg.get_value(W["dl_video_fmt"])
        fmt_sel = _VIDEO_FMT_SEL.get(dpg.get_value(W["dl_video_quality"]),
                                      "bestvideo+bestaudio/best")
        cmd     = [ytdlp,
                   "-f", fmt_sel,
                   "--merge-output-format", fmt,
                   "--newline", pl_flag,
                   "-o", os.path.join(dest, "%(title)s.%(ext)s"),
                   url]

    with _dl_log_lock:
        _dl_log_lines = []
    _ui(dpg.set_value,      W["dl_log"],      "")
    _ui(dpg.set_value,      W["dl_status"],   "Iniciando download...")
    _ui(dpg.set_value,      W["dl_progress"], 0.0)
    _ui(dpg.configure_item, W["dl_run_btn"],  enabled=False)
    _ui(dpg.configure_item, W["dl_stop_btn"], enabled=True)

    def _do():
        global _dl_process
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            with _dl_process_lock:
                _dl_process = proc
            for raw in proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                _dl_append_log(line)
                m = re.search(r'\[download\]\s+([\d.]+)%', line)
                if m:
                    _ui(dpg.set_value, W["dl_progress"],
                        min(float(m.group(1)) / 100.0, 1.0))
                _ui(dpg.set_value, W["dl_status"], line[:100])

            proc.wait()
            rc = proc.returncode
            if rc == 0:
                _ui(dpg.set_value, W["dl_status"],        "Concluído OK")
                _ui(dpg.set_value, W["dl_progress"],      1.0)
                _ui(dpg.set_value, W["dl_search_status"], "Download concluído OK")
            else:
                _ui(dpg.set_value, W["dl_status"],        f"Encerrado com erro (código {rc})")
                _ui(dpg.set_value, W["dl_search_status"], f"Erro no download (código {rc})")
        except Exception as e:
            traceback.print_exc()
            _ui(dpg.set_value, W["dl_status"], f"Erro: {e}")
        finally:
            with _dl_process_lock:
                _dl_process = None
            _ui(dpg.configure_item, W["dl_run_btn"],  enabled=True)
            _ui(dpg.configure_item, W["dl_stop_btn"], enabled=False)

    threading.Thread(target=_do, daemon=True).start()


def _build_download_tab():
    dpg.add_text("Baixar Música", color=ACCENT)
    dpg.add_separator()
    dpg.add_spacer(height=6)

    # ── Aviso de instalação ───────────────────────────────────────────
    with dpg.group(horizontal=True, show=not bool(_find_ytdlp())) as _warn:
        dpg.add_text("  yt-dlp não encontrado.", color=YELLOW)
        dpg.add_spacer(width=10)
        W["dl_install_btn"] = dpg.add_button(
            label=" Instalar yt-dlp ", callback=lambda *_: _dl_install_ytdlp(),
        )
    W["dl_ytdlp_warn"] = _warn
    dpg.add_spacer(height=4)

    # ── Pesquisa YouTube ──────────────────────────────────────────────
    with dpg.collapsing_header(label="  Pesquisar no YouTube", default_open=True):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            W["dl_search_input"] = dpg.add_input_text(
                width=-120, hint="nome da música, artista...",
                on_enter=True, callback=lambda *_: _run_search(),
            )
            W["dl_search_btn"] = dpg.add_button(
                label=" Pesquisar ", callback=lambda *_: _run_search(),
            )
        dpg.add_spacer(height=4)
        W["dl_search_status"] = dpg.add_text("", color=DIM)
        dpg.add_spacer(height=4)
        W["dl_search_table_container"] = dpg.add_group()

    dpg.add_spacer(height=8)

    # ── Baixar por URL ────────────────────────────────────────────────
    with dpg.collapsing_header(label="  Baixar por URL", default_open=True) as _url_sec:
        dpg.add_spacer(height=6)
        dpg.add_text(
            "Suporta YouTube, SoundCloud, Bandcamp e centenas de outros sites."
            " Playlists são detectadas automaticamente pela URL.",
            color=DIM, wrap=700,
        )
        dpg.add_spacer(height=8)

        with dpg.group(horizontal=True):
            dpg.add_text("URL:", color=DIM)
            W["dl_url"] = dpg.add_input_text(
                width=-80, hint="https://youtube.com/watch?v=...",
            )
            dpg.add_button(label=" Colar ", callback=lambda *_: _dl_paste_url())

        dpg.add_spacer(height=8)

        with dpg.group(horizontal=True):
            dpg.add_text("Tipo:", color=DIM)
            W["dl_type"] = dpg.add_radio_button(
                items=["Áudio", "Vídeo"],
                default_value="Áudio",
                horizontal=True,
                callback=lambda *_: _dl_update_format_visibility(),
            )
            dpg.add_spacer(width=24)
            W["dl_playlist"] = dpg.add_checkbox(
                label="Baixar playlist completa", default_value=True,
            )

        dpg.add_spacer(height=6)

        with dpg.group(horizontal=True) as _ag:
            dpg.add_text("Formato:", color=DIM)
            W["dl_audio_fmt"] = dpg.add_combo(
                items=["mp3", "m4a", "flac", "wav", "opus"],
                default_value="mp3", width=90,
            )
            dpg.add_spacer(width=16)
            dpg.add_text("Qualidade:", color=DIM)
            W["dl_audio_quality"] = dpg.add_combo(
                items=["Melhor (320k)", "Alta (256k)", "Média (192k)", "Baixa (128k)"],
                default_value="Melhor (320k)", width=150,
            )
        W["dl_audio_grp"] = _ag

        with dpg.group(horizontal=True, show=False) as _vg:
            dpg.add_text("Formato:", color=DIM)
            W["dl_video_fmt"] = dpg.add_combo(
                items=["mp4", "webm", "mkv"],
                default_value="mp4", width=90,
            )
            dpg.add_spacer(width=16)
            dpg.add_text("Qualidade:", color=DIM)
            W["dl_video_quality"] = dpg.add_combo(
                items=["Melhor", "1080p", "720p", "480p", "360p"],
                default_value="Melhor", width=110,
            )
        W["dl_video_grp"] = _vg

        dpg.add_spacer(height=6)

        with dpg.group(horizontal=True):
            dpg.add_button(
                label=" Pasta destino ", callback=lambda *_: _dl_pick_folder(),
            )
            W["dl_dest_text"] = dpg.add_text(_dl_dest_folder, color=DIM)

        dpg.add_spacer(height=10)

        with dpg.group(horizontal=True):
            W["dl_run_btn"]  = dpg.add_button(
                label="  Baixar  ", callback=lambda *_: _run_download(),
            )
            W["dl_stop_btn"] = dpg.add_button(
                label="  Parar  ", callback=lambda *_: _stop_download(), enabled=False,
            )

        dpg.add_spacer(height=8)
        W["dl_status"]   = dpg.add_text("", color=DIM)
        W["dl_progress"] = dpg.add_progress_bar(default_value=0.0, width=-1)
        dpg.add_spacer(height=6)

        dpg.add_text("Log:", color=DIM)
        W["dl_log"] = dpg.add_input_text(
            multiline=True, readonly=True, width=-1, height=200,
            default_value="",
        )
    W["dl_url_section"] = _url_sec


# ── Auto-tagging ──────────────────────────────────────────────────────
def _enable_tag_buttons():
    _ui(dpg.configure_item, W["tag_run_btn"],    enabled=True)
    _ui(dpg.configure_item, W["tag_rename_btn"], enabled=True)


def _open_tag_target():
    def _pick():
        try:
            path = _win_open_file("Selecionar arquivo para tagar")
            if path:
                _ui(dpg.set_value, W["tag_target_text"], path)
                _enable_tag_buttons()
        except Exception as e:
            _ui(dpg.set_value, W["tag_status"], f"Erro: {e}")
    threading.Thread(target=_pick, daemon=True).start()


def _open_tag_folder():
    def _pick():
        try:
            folder = _win_open_folder("Selecionar pasta para tagar")
            if folder:
                _ui(dpg.set_value, W["tag_target_text"], folder)
                _enable_tag_buttons()
        except Exception as e:
            _ui(dpg.set_value, W["tag_status"], f"Erro: {e}")
    threading.Thread(target=_pick, daemon=True).start()


def _run_tag():
    target      = dpg.get_value(W["tag_target_text"])
    dry_run     = dpg.get_value(W["tag_dry_run"])
    apply_year  = dpg.get_value(W["tag_apply_year"])
    apply_cover = dpg.get_value(W["tag_apply_cover"])
    apply_genre = dpg.get_value(W["tag_apply_genre"])
    do_rename   = dpg.get_value(W["tag_rename"])

    if not target:
        return

    _ui(dpg.set_value,      W["tag_status"],   "Processando...")
    _ui(dpg.set_value,      W["tag_progress"], 0.0)
    _ui(dpg.configure_item, W["tag_run_btn"],  enabled=False)

    def _do():
        try:
            from tagger import rename_file
            fetch_year  = apply_year
            fetch_cover = apply_cover
            fetch_genre = apply_genre
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
                result = tag_file(
                    fpath, dry_run=dry_run,
                    fetch_year_online=fetch_year, fetch_cover=fetch_cover,
                    fetch_genre=fetch_genre,
                )
                # Rename: renomeia o arquivo após tagar com sucesso
                if do_rename and result.get('written') and not dry_run:
                    actual_path = result.get('path', fpath)
                    rr = rename_file(actual_path, dry_run=False, title_case=True)
                    result['rename_from'] = rr.get('file')
                    result['rename_to']   = rr.get('new_name')
                    result['renamed']     = rr.get('renamed', False)
                    if rr.get('renamed') and rr.get('path'):
                        result['path'] = rr['path']
                elif do_rename and dry_run:
                    rr = rename_file(fpath, dry_run=True, title_case=True)
                    result['rename_from'] = rr.get('file')
                    result['rename_to']   = rr.get('new_name')
                    result['renamed']     = rr.get('renamed', False)
                results.append(result)

            _ui(_apply_tag_table, results, dry_run)
        except Exception as e:
            traceback.print_exc()
            _ui(dpg.set_value, W["tag_status"], f"Erro: {e}")
        finally:
            _ui(dpg.configure_item, W["tag_run_btn"], enabled=True)

    threading.Thread(target=_do, daemon=True).start()


def _run_rename_only():
    """Rename standalone — sem tagging, só limpa nomes."""
    target    = dpg.get_value(W["tag_target_text"])
    dry_run   = dpg.get_value(W["tag_dry_run"])

    if not target:
        return

    _ui(dpg.set_value,      W["tag_status"],   "Renomeando...")
    _ui(dpg.set_value,      W["tag_progress"], 0.0)
    _ui(dpg.configure_item, W["tag_rename_btn"], enabled=False)

    def _do():
        try:
            from tagger import rename_file, rename_folder
            if os.path.isfile(target):
                results = [rename_file(target, dry_run=dry_run, title_case=True)]
            else:
                results = rename_folder(target, dry_run=dry_run, title_case=True)

            _ui(_apply_rename_table, results, dry_run)
        except Exception as e:
            traceback.print_exc()
            _ui(dpg.set_value, W["tag_status"], f"Erro: {e}")
        finally:
            _ui(dpg.configure_item, W["tag_rename_btn"], enabled=True)

    threading.Thread(target=_do, daemon=True).start()


def _apply_tag_table(results: list[dict], dry_run: bool):
    for tex_tag in _cover_texture_tags:
        try:
            dpg.delete_item(tex_tag)
        except Exception:
            pass
    _cover_texture_tags.clear()

    prev = W.get("tag_table")
    if prev:
        try:
            dpg.delete_item(prev)
        except Exception:
            pass

    ok       = sum(1 for r in results if r.get("written"))
    err      = sum(1 for r in results if r.get("error") and not r.get("written"))
    renamed  = sum(1 for r in results if r.get("renamed"))
    mode = " (dry-run)" if dry_run else ""
    status_parts = [f"{ok} tagado(s)", f"{err} erro(s)"]
    if renamed:
        status_parts.append(f"{renamed} renomeado(s)")
    dpg.set_value(W["tag_status"],
                  f"Concluído{mode} — {'  '.join(status_parts)}")

    container = W["tag_table_container"]
    has_rename = any(r.get("rename_to") for r in results)

    tbl = dpg.add_table(
        parent=container,
        header_row=True,
        borders_innerH=True,
        borders_outerH=True,
        borders_outerV=True,
        row_background=True,
    )
    W["tag_table"] = tbl

    cols = ["Arquivo", "Artista", "Título", "Gênero", "Ano", "Capa", "Status"]
    if has_rename:
        cols.append("Novo nome")
    for col in cols:
        dpg.add_table_column(parent=tbl, label=col)

    for r in results:
        row = dpg.add_table_row(parent=tbl)
        dpg.add_text((r.get("file") or "")[:38],  parent=row)
        dpg.add_text(r.get("artist") or "—",     parent=row, color=ACCENT)
        dpg.add_text(r.get("title") or "—",      parent=row, color=ACCENT2)
        dpg.add_text(r.get("genre") or "—",      parent=row, color=GREEN)
        dpg.add_text(r.get("year") or "—",       parent=row, color=YELLOW)

        cover_data = r.get("cover_preview")
        if cover_data:
            tex = _register_cover_texture(cover_data)
            if tex:
                dpg.add_image(tex, parent=row, width=48, height=48)
            else:
                dpg.add_text("OK" if r.get("cover_written") else "img?", parent=row, color=GREEN)
        elif r.get("cover_written"):
            dpg.add_text("OK", parent=row, color=GREEN)
        else:
            dpg.add_text("—", parent=row, color=DIM)

        if r.get("error") and not r.get("written"):
            dpg.add_text(r["error"][:30], parent=row, color=RED)
        elif r.get("written"):
            label = "preview" if dry_run else "OK"
            dpg.add_text(label, parent=row, color=YELLOW if dry_run else GREEN)
        else:
            dpg.add_text("—", parent=row, color=DIM)

        if has_rename:
            rto = r.get("rename_to") or ""
            if r.get("renamed") and rto and rto != r.get("file"):
                dpg.add_text(rto[:38], parent=row, color=ACCENT)
            else:
                dpg.add_text("—", parent=row, color=DIM)


def _apply_rename_table(results: list[dict], dry_run: bool):
    prev = W.get("tag_table")
    if prev:
        try:
            dpg.delete_item(prev)
        except Exception:
            pass

    renamed   = sum(1 for r in results if r.get("renamed"))
    unchanged = sum(1 for r in results if not r.get("renamed") and not r.get("error"))
    errors    = sum(1 for r in results if r.get("error"))
    mode = " (dry-run)" if dry_run else ""
    dpg.set_value(W["tag_status"],
                  f"Rename{mode} — {renamed} renomeado(s)  "
                  f"{unchanged} inalterado(s)  {errors} erro(s)")
    dpg.set_value(W["tag_progress"], 1.0)

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

    for col in ["Antes", "Depois", "Status"]:
        dpg.add_table_column(parent=tbl, label=col)

    for r in results:
        row = dpg.add_table_row(parent=tbl)
        old_name = r.get("file", "")
        new_name = r.get("new_name") or "—"

        if r.get("error"):
            dpg.add_text(old_name[:45], parent=row, color=RED)
            dpg.add_text("—",          parent=row, color=DIM)
            dpg.add_text(r["error"][:30], parent=row, color=RED)
        elif r.get("renamed") and new_name != old_name:
            dpg.add_text(old_name[:45], parent=row, color=DIM)
            dpg.add_text(new_name[:45], parent=row, color=ACCENT)
            label = "preview" if dry_run else "OK"
            dpg.add_text(label, parent=row, color=YELLOW if dry_run else GREEN)
        else:
            dpg.add_text(old_name[:45], parent=row, color=DIM)
            dpg.add_text("= igual",    parent=row, color=DIM)
            dpg.add_text("—",          parent=row, color=DIM)


# ── Construtores UI ───────────────────────────────────────────────────
def _build_info_panel(slot: str, label: str, color):
    dpg.add_text(label, color=color)
    W[f"file_{slot}"]   = dpg.add_text("Nenhum arquivo", color=DIM, wrap=290)
    W[f"status_{slot}"] = dpg.add_text("")
    dpg.add_button(label="  Abrir arquivo  ", callback=lambda *_: _open_file(slot))
    dpg.add_separator()

    for key, wkey in [("BPM", "bpm"), ("Tom", "tom"), ("Duração", "dur")]:
        with dpg.group(horizontal=True):
            dpg.add_text(f"{key}:", color=DIM)
            W[f"{wkey}_{slot}"] = dpg.add_text("—")

    # Análise local
    dpg.add_spacer(height=8)
    dpg.add_text("Análise local", color=ACCENT)
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text("Gênero:",    color=DIM)
        W[f"genero_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Subgênero:", color=DIM)
        W[f"sub_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Confiança:", color=DIM)
        W[f"conf_{slot}"] = dpg.add_text("—")
    dpg.add_text("Top candidatos:", color=DIM)
    for i in range(3):
        W[f"cand_{slot}_{i}"] = dpg.add_text("", color=WHITE, wrap=290)

    # Spotify
    dpg.add_spacer(height=8)
    dpg.add_text("Spotify", color=SP_GREEN)
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text("Gênero:",    color=DIM)
        W[f"sp_genre_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Subgênero:", color=DIM)
        W[f"sp_sub_{slot}"] = dpg.add_text("—")
    W[f"sp_tags_{slot}"]  = dpg.add_text("—", color=DIM, wrap=290)
    W[f"sp_feats_{slot}"] = dpg.add_text("—", color=DIM, wrap=290)

    # Last.fm
    dpg.add_spacer(height=8)
    dpg.add_text("Last.fm", color=LFM_RED)
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text("Gênero:",    color=DIM)
        W[f"lfm_genre_{slot}"] = dpg.add_text("—")
    with dpg.group(horizontal=True):
        dpg.add_text("Subgênero:", color=DIM)
        W[f"lfm_sub_{slot}"] = dpg.add_text("—")
    W[f"lfm_tags_{slot}"] = dpg.add_text("—", color=DIM, wrap=290)

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
    dpg.add_text("Espectro de Frequências", color=color)
    dpg.add_separator()
    for i, bar_color in enumerate(_SPEC_COLORS):
        W[f"barlbl_{slot}_{i}"] = dpg.add_text("—", color=bar_color)
        pb = dpg.add_progress_bar(default_value=0.0, width=-1)
        with dpg.theme() as _t:
            with dpg.theme_component(dpg.mvProgressBar):
                dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, bar_color)
        dpg.bind_item_theme(pb, _t)
        W[f"bar_{slot}_{i}"] = pb
        dpg.add_spacer(height=2)

    dpg.add_spacer(height=8)
    dpg.add_text("Waveform", color=DIM)
    _x0 = list(range(_WAVE_N))
    _y0 = [0.0] * _WAVE_N
    with dpg.plot(height=100, width=-1, no_title=True, no_mouse_pos=True) as _plot:
        W[f"wave_xax_{slot}"] = dpg.add_plot_axis(
            dpg.mvXAxis,
            no_gridlines=True, no_tick_marks=True, no_tick_labels=True,
        )
        with dpg.plot_axis(
            dpg.mvYAxis,
            no_gridlines=True, no_tick_marks=True, no_tick_labels=True,
        ) as _yax:
            W[f"wave_{slot}"] = dpg.add_shade_series(_x0, _y0, y2=_y0)
            W[f"wave_yax_{slot}"] = _yax
    dpg.set_axis_limits(W[f"wave_xax_{slot}"], 0, _WAVE_N - 1)
    dpg.set_axis_limits(W[f"wave_yax_{slot}"], -1.0, 1.0)
    with dpg.theme() as _pt:
        with dpg.theme_component(dpg.mvShadeSeries):
            dpg.add_theme_color(dpg.mvPlotCol_Fill, (86, 156, 214, 160),
                                category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_Line, (140, 195, 250, 220),
                                category=dpg.mvThemeCat_Plots)
    dpg.bind_item_theme(_plot, _pt)


# ── Aba Treinar ───────────────────────────────────────────────────────

def _train_log_append(msg: str):
    global _train_log_lines
    with _train_log_lock:
        _train_log_lines.append(msg)
        if len(_train_log_lines) > 300:
            _train_log_lines = _train_log_lines[-300:]
        text = "\n".join(_train_log_lines)
    dpg.set_value(W["train_log"], text)


def _train_update_model_info():
    if os.path.exists("model.pkl"):
        mtime = os.path.getmtime("model.pkl")
        ts    = time.strftime("%d/%m/%Y %H:%M", time.localtime(mtime))
        dpg.set_value(W["train_model_info"], f"model.pkl encontrado  ({ts})")
        dpg.configure_item(W["audit_btn"], enabled=True)
    else:
        dpg.set_value(W["train_model_info"], "model.pkl nao encontrado")
        dpg.configure_item(W["audit_btn"], enabled=False)


CHECKPOINT_INTERVAL = 50  # salva progresso a cada N faixas


def _on_train_restart():
    from trainer import clear_checkpoint, CHECKPOINT_PATH
    clear_checkpoint()
    with _train_log_lock:
        _train_log_lines.clear()
    dpg.set_value(W["train_log"], "")
    dpg.set_value(W["train_status"], "Checkpoint removido. Pronto para recomecar do zero.")


def _run_train_thread(dataset_dir: str, n_estimators: int):
    global _train_log_lines, _train_stop_flag
    from trainer import scan_dataset, NUMERIC_FEATURES, save_checkpoint, load_checkpoint, clear_checkpoint
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import cross_val_score
        import pickle
    except ImportError as e:
        _ui(_train_log_append, f"Dependencia ausente: {e}  (pip install scikit-learn)")
        _ui(dpg.configure_item, W["train_btn"], enabled=True)
        return

    def log(msg):
        _ui(_train_log_append, msg)

    try:
        _train_stop_flag.clear()
        _ui(dpg.configure_item, W["train_btn"], enabled=False)
        _ui(dpg.configure_item, W["train_pause_btn"], enabled=True)
        _ui(dpg.configure_item, W["train_restart_btn"], enabled=False)
        with _train_log_lock:
            _train_log_lines.clear()
        _ui(dpg.set_value, W["train_log"], "")
        _ui(dpg.set_value, W["train_status"], "Verificando checkpoint...")

        ckpt = load_checkpoint(dataset_dir)
        if ckpt:
            items      = ckpt['items']
            done_paths = ckpt['done_paths']
            X          = ckpt['X']
            y_labels   = ckpt['y_labels']
            errors     = ckpt['errors']
            log(f"Retomando checkpoint: {len(done_paths)} processadas ({len(X)} ok, {errors} erros) de {len(items)}. Restam {len(items) - len(done_paths)}.")
        else:
            _ui(dpg.set_value, W["train_status"], "Escaneando dataset...")
            items = scan_dataset(dataset_dir)
            if not items:
                log("Nenhuma faixa encontrada. Verifique a estrutura: genre/subgenre/arquivo.mp3")
                _ui(dpg.set_value, W["train_status"], "Dataset vazio.")
                return
            log(f"{len(items)} faixas encontradas.")
            done_paths = set()
            X, y_labels, errors = [], [], 0

        remaining = [item for item in items if item[0] not in done_paths]
        initial_done = len(done_paths)

        for i, (path, genre, subgenre) in enumerate(remaining):
            if _train_stop_flag.is_set():
                save_checkpoint(dataset_dir, items, done_paths, X, y_labels, errors)
                total_done = initial_done + i
                log(f"Pausado. Progresso salvo: {total_done}/{len(items)} faixas.")
                _ui(dpg.set_value, W["train_status"], f"Pausado em {total_done}/{len(items)}. Clique Treinar para continuar.")
                return

            global_i = initial_done + i + 1
            _ui(dpg.set_value, W["train_status"], f"Extraindo features... {global_i}/{len(items)}")
            try:
                features = analyze_file(path)
                X.append([features.get(k, 0) for k in NUMERIC_FEATURES])
                y_labels.append(f"{genre}|{subgenre}")
            except Exception as e:
                errors += 1
                log(f"  Erro: {os.path.basename(path)}: {e}")
            done_paths.add(path)

            if (i + 1) % CHECKPOINT_INTERVAL == 0:
                save_checkpoint(dataset_dir, items, done_paths, X, y_labels, errors)
                log(f"  [checkpoint salvo] {global_i}/{len(items)}")

        if errors:
            log(f"{errors} faixas com erro ignoradas.")

        if len(X) < 10:
            log("Dados insuficientes (minimo 10 faixas validas).")
            _ui(dpg.set_value, W["train_status"], "Dados insuficientes.")
            return

        X_arr = np.array(X)
        y_arr = np.array(y_labels)
        n_classes = len(set(y_labels))

        _ui(dpg.set_value, W["train_status"], "Normalizando e validando...")
        scaler   = StandardScaler()
        X_scaled = scaler.fit_transform(X_arr)

        model  = RandomForestClassifier(n_estimators=n_estimators, random_state=42, n_jobs=-1)
        cv     = min(5, n_classes)
        scores = cross_val_score(model, X_scaled, y_arr, cv=cv)
        acc    = f"Acuracia {cv}-fold: {scores.mean():.1%} +/- {scores.std():.1%}"
        log(acc)

        _ui(dpg.set_value, W["train_status"], "Treinando modelo final...")
        model.fit(X_scaled, y_arr)

        bundle = {
            'model': model, 'scaler': scaler,
            'feature_names': NUMERIC_FEATURES,
            'label_names': model.classes_.tolist(),
        }
        with open('model.pkl', 'wb') as f:
            pickle.dump(bundle, f)

        clear_checkpoint()

        log(f"Modelo salvo: model.pkl")
        log(f"Generos treinados: {n_classes}")
        for g in sorted(set(y_labels)):
            log(f"  - {g.replace('|', ' / ')}")

        _ui(dpg.set_value, W["train_status"], f"Concluido. {acc}")
        _ui(_train_update_model_info)

    except Exception as e:
        traceback.print_exc()
        log(f"Erro: {e}")
        _ui(dpg.set_value, W["train_status"], f"Erro: {e}")
    finally:
        _ui(dpg.configure_item, W["train_btn"], enabled=True)
        _ui(dpg.configure_item, W["train_pause_btn"], enabled=False)
        _ui(dpg.configure_item, W["train_restart_btn"], enabled=True)


def _run_audit_thread(dataset_dir: str, threshold: float):
    global _audit_results
    from trainer import scan_dataset
    from classifier import classify_ml
    try:
        _ui(dpg.configure_item, W["audit_btn"], enabled=False)
        _ui(dpg.set_value, W["audit_status"], "Escaneando dataset...")

        items = scan_dataset(dataset_dir)
        if not items:
            _ui(dpg.set_value, W["audit_status"], "Nenhuma faixa encontrada no dataset.")
            return

        suspects, errors = [], 0
        for i, (path, genre, subgenre) in enumerate(items):
            _ui(dpg.set_value, W["audit_status"], f"Analisando... {i+1}/{len(items)}")
            try:
                features   = analyze_file(path)
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

        _audit_results = suspects
        _ui(_apply_audit_table, suspects, len(items), errors)

    except Exception as e:
        _ui(dpg.set_value, W["audit_status"], f"Erro: {e}")
    finally:
        _ui(dpg.configure_item, W["audit_btn"], enabled=True)


def _apply_audit_table(suspects: list, total: int, errors: int):
    prev = W.get("audit_table")
    if prev and dpg.does_item_exist(prev):
        dpg.delete_item(prev)

    msg = f"{len(suspects)} suspeito(s) de {total} faixas"
    if errors:
        msg += f"  ({errors} com erro)"
    dpg.set_value(W["audit_status"], msg)
    dpg.configure_item(W["audit_export_btn"], enabled=bool(suspects))

    if not suspects:
        return

    container = W["audit_table_container"]
    with dpg.table(
        header_row=True, borders_innerH=True, borders_outerH=True,
        borders_outerV=True, row_background=True, width=-1,
        parent=container,
    ) as tbl:
        W["audit_table"] = tbl
        dpg.add_table_column(label="Arquivo",       width_fixed=True, init_width_or_weight=220)
        dpg.add_table_column(label="Pasta (declarado)", width_fixed=True, init_width_or_weight=200)
        dpg.add_table_column(label="Modelo diz",    width_fixed=True, init_width_or_weight=200)
        dpg.add_table_column(label="Confianca",     width_fixed=True, init_width_or_weight=80)
        for s in suspects:
            with dpg.table_row():
                dpg.add_text(s['file'],           color=WHITE)
                dpg.add_text(f"{s['declared_genre']} / {s['declared_subgenre']}", color=DIM)
                dpg.add_text(f"{s['model_genre']} / {s['model_subgenre']}",       color=YELLOW)
                dpg.add_text(f"{s['confidence']:.0%}", color=ACCENT)


def _export_audit():
    if not _audit_results:
        return

    def _do():
        path = _win_save_file("Exportar auditoria", "CSV\0*.csv\0", "csv")
        if not path:
            return
        with open(path, 'w', newline='', encoding='utf-8') as fp:
            writer = csv.DictWriter(fp, fieldnames=list(_audit_results[0].keys()))
            writer.writeheader()
            writer.writerows(_audit_results)
        _ui(dpg.set_value, W["audit_status"], f"Exportado: {os.path.basename(path)}")

    threading.Thread(target=_do, daemon=True).start()


def _build_train_tab():
    dpg.add_text("Treinar Modelo ML", color=ACCENT)
    dpg.add_separator()
    dpg.add_spacer(height=6)
    dpg.add_text(
        "Estrutura aceita:  pasta/Genero/Subgenero/arquivo.mp3  ou  pasta/Subgenero/arquivo.mp3",
        color=DIM, wrap=750,
    )
    dpg.add_spacer(height=8)

    # Dataset e estimators
    with dpg.group(horizontal=True):
        dpg.add_text("Dataset:", color=DIM)
        W["train_folder_text"] = dpg.add_input_text(
            hint="Pasta raiz do dataset", width=420, readonly=True,
        )
        dpg.add_button(label=" Selecionar ",
                       callback=lambda: threading.Thread(
                           target=lambda: _ui(dpg.set_value, W["train_folder_text"],
                                              _win_open_folder("Selecionar dataset") or
                                              dpg.get_value(W["train_folder_text"])),
                           daemon=True).start())

    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        dpg.add_text("Arvores (estimators):", color=DIM)
        W["train_estimators"] = dpg.add_slider_int(
            min_value=50, max_value=500, default_value=200, width=200,
        )
        dpg.add_text("(mais = mais preciso, mais lento)", color=DIM)

    dpg.add_spacer(height=8)
    with dpg.group(horizontal=True):
        W["train_btn"] = dpg.add_button(
            label="  Treinar  ",
            callback=lambda: threading.Thread(
                target=_run_train_thread,
                args=(dpg.get_value(W["train_folder_text"]),
                      dpg.get_value(W["train_estimators"])),
                daemon=True,
            ).start() if dpg.get_value(W["train_folder_text"]) else None,
        )
        dpg.add_spacer(width=8)
        W["train_pause_btn"] = dpg.add_button(
            label="  Pausar  ",
            callback=lambda: _train_stop_flag.set(),
            enabled=False,
        )
        dpg.add_spacer(width=8)
        W["train_restart_btn"] = dpg.add_button(
            label="  Recomecar do zero  ",
            callback=_on_train_restart,
        )
        dpg.add_spacer(width=16)
        W["train_model_info"] = dpg.add_text("", color=DIM)

    dpg.add_spacer(height=4)
    W["train_status"] = dpg.add_text("", color=DIM)
    dpg.add_spacer(height=4)
    W["train_log"] = dpg.add_input_text(
        multiline=True, readonly=True, width=-1, height=160,
        default_value="",
    )

    dpg.add_spacer(height=16)
    dpg.add_text("Auditar Dataset", color=ACCENT2)
    dpg.add_separator()
    dpg.add_spacer(height=6)
    dpg.add_text(
        "Analisa cada faixa com o modelo e lista suspeitos de rótulo errado (alta confiança + discordância).",
        color=DIM, wrap=750,
    )
    dpg.add_spacer(height=8)

    with dpg.group(horizontal=True):
        dpg.add_text("Dataset:", color=DIM)
        W["audit_folder_text"] = dpg.add_input_text(
            hint="Mesma pasta do treino", width=420, readonly=True,
        )
        dpg.add_button(label=" Selecionar ",
                       callback=lambda: threading.Thread(
                           target=lambda: _ui(dpg.set_value, W["audit_folder_text"],
                                              _win_open_folder("Selecionar dataset") or
                                              dpg.get_value(W["audit_folder_text"])),
                           daemon=True).start())

    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        dpg.add_text("Confianca minima:", color=DIM)
        W["audit_threshold"] = dpg.add_slider_float(
            min_value=0.5, max_value=1.0, default_value=0.8,
            format="%.2f", width=200,
        )
        dpg.add_text("(so reporta se o modelo tiver alta certeza)", color=DIM)

    dpg.add_spacer(height=8)
    with dpg.group(horizontal=True):
        W["audit_btn"] = dpg.add_button(
            label="  Auditar  ",
            callback=lambda: threading.Thread(
                target=_run_audit_thread,
                args=(dpg.get_value(W["audit_folder_text"]),
                      dpg.get_value(W["audit_threshold"])),
                daemon=True,
            ).start() if dpg.get_value(W["audit_folder_text"]) else None,
            enabled=False,
        )
        dpg.add_spacer(width=8)
        W["audit_export_btn"] = dpg.add_button(
            label=" Exportar CSV ", callback=_export_audit, enabled=False,
        )

    dpg.add_spacer(height=4)
    W["audit_status"] = dpg.add_text("", color=DIM)
    dpg.add_spacer(height=6)
    W["audit_table_container"] = dpg.add_group()

    _train_update_model_info()


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
                W[f"cmp_a_{i}"]   = dpg.add_text("", color=ACCENT)
                W[f"cmp_b_{i}"]   = dpg.add_text("", color=ACCENT2)
                W[f"cmp_d_{i}"]   = dpg.add_text("", color=WHITE)

    # ── Painel de Compatibilidade DJ ──────────────────────────────
    dpg.add_spacer(height=10)
    dpg.add_separator()
    dpg.add_spacer(height=6)
    dpg.add_text("Compatibilidade para Mixagem", color=ACCENT)
    dpg.add_spacer(height=6)

    with dpg.group(horizontal=True):
        W["compat_score"]  = dpg.add_text("—", color=DIM)
        dpg.add_spacer(width=8)
        W["compat_rating"] = dpg.add_text("", color=DIM)

    W["compat_bar"] = dpg.add_progress_bar(default_value=0.0, width=-1)
    dpg.add_spacer(height=8)

    with dpg.table(header_row=True, borders_innerH=True,
                   borders_outerV=True, row_background=True, width=-1):
        dpg.add_table_column(label="Critério")
        dpg.add_table_column(label="Score")
        for label, wkey in [("BPM (35%)", "compat_bpm"),
                            ("Tom (30%)", "compat_key"),
                            ("Energia (20%)", "compat_energy"),
                            ("Gênero (15%)", "compat_genre")]:
            with dpg.table_row():
                dpg.add_text(label, color=DIM)
                W[wkey] = dpg.add_text("—", color=WHITE)

    dpg.add_spacer(height=8)
    dpg.add_text("Dicas", color=ACCENT)
    W["compat_tips"] = dpg.add_text("", color=DIM, wrap=500)


def _build_tag_tab():
    dpg.add_text("Auto-tagging & Rename", color=ACCENT)
    dpg.add_separator()
    dpg.add_spacer(height=6)
    dpg.add_text(
        "Lê o nome do arquivo (\"Artista - Título.ext\"), grava tags ID3/Vorbis "
        "e pode renomear o arquivo limpando lixo do nome.",
        color=DIM, wrap=700,
    )
    dpg.add_spacer(height=8)

    with dpg.group(horizontal=True):
        dpg.add_button(label="  Arquivo  ", callback=lambda *_: _open_tag_target())
        dpg.add_button(label="  Pasta  ",   callback=lambda *_: _open_tag_folder())
        W["tag_target_text"] = dpg.add_text("Nenhum alvo selecionado", color=DIM)

    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        W["tag_dry_run"]    = dpg.add_checkbox(label="Dry-run (não grava)")
        dpg.add_spacer(width=16)
        W["tag_apply_year"] = dpg.add_checkbox(label="Aplicar ano", default_value=True)
        dpg.add_spacer(width=16)
        W["tag_apply_cover"] = dpg.add_checkbox(label="Aplicar capa", default_value=True)
        dpg.add_spacer(width=16)
        W["tag_apply_genre"] = dpg.add_checkbox(label="Buscar gênero")
        dpg.add_spacer(width=16)
        W["tag_rename"]     = dpg.add_checkbox(label="Renomear arquivo")

    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        W["tag_run_btn"] = dpg.add_button(
            label="  Tagar  ",
            callback=lambda *_: _run_tag(),
            enabled=False,
        )
        dpg.add_spacer(width=12)
        W["tag_rename_btn"] = dpg.add_button(
            label="  Só Renomear  ",
            callback=lambda *_: _run_rename_only(),
            enabled=False,
        )
        dpg.add_spacer(width=16)
        dpg.add_text("Só renomear: limpa lixo do nome sem buscar tags online",
                     color=DIM)

    dpg.add_spacer(height=8)
    W["tag_status"]   = dpg.add_text("", color=DIM)
    W["tag_progress"] = dpg.add_progress_bar(default_value=0.0, width=-1)
    dpg.add_spacer(height=6)
    W["tag_table_container"] = dpg.add_group()


def _build_batch_tab():
    dpg.add_text("Análise em Lote", color=ACCENT)
    dpg.add_separator()
    dpg.add_spacer(height=6)

    with dpg.group(horizontal=True):
        dpg.add_button(label="  Selecionar pasta  ",
                       callback=lambda *_: _open_batch_folder())
        W["batch_folder_text"] = dpg.add_text("Nenhuma pasta selecionada",
                                               color=DIM)

    dpg.add_spacer(height=8)
    W["batch_status"]   = dpg.add_text("", color=DIM)
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


# ── Splash ────────────────────────────────────────────────────────────
def _build_splash():
    sw, sh = 500, 340
    vw, vh = 1280, 800
    px, py  = (vw - sw) // 2, (vh - sh) // 2

    with dpg.window(tag="splash_window", no_title_bar=True, no_move=True,
                    no_resize=True, no_scrollbar=True, no_collapse=True,
                    width=sw, height=sh, pos=[px, py]):
        with dpg.theme() as _t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (14, 10, 24, 252))
                dpg.add_theme_color(dpg.mvThemeCol_Border,   (170, 40, 255, 200))
                dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 2)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 10)
        dpg.bind_item_theme("splash_window", _t)

        dpg.add_spacer(height=28)
        dpg.add_text(APP_NAME,        color=ACCENT,  indent=20)
        dpg.add_text(APP_DESCRIPTION, color=DIM,     indent=20)
        dpg.add_spacer(height=6)
        dpg.add_separator()
        dpg.add_spacer(height=10)
        dpg.add_text(COMPANY_NAME,    color=PURPLE,  indent=20)
        dpg.add_text(f"Versão {APP_VERSION}", color=DIM, indent=20)
        dpg.add_spacer(height=28)
        dpg.add_separator()
        dpg.add_spacer(height=8)
        dpg.add_text(APP_COPYRIGHT,   color=DIM,     indent=20)
        dpg.add_spacer(height=20)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=sw - 160)
            dpg.add_button(
                label="  Continuar  ",
                callback=lambda: dpg.delete_item("splash_window"),
            )


# ── Sobre ─────────────────────────────────────────────────────────────
def _build_about_tab():
    dpg.add_spacer(height=20)
    dpg.add_text(APP_NAME,        color=ACCENT)
    dpg.add_text(APP_DESCRIPTION, color=DIM)
    dpg.add_spacer(height=4)
    dpg.add_separator()
    dpg.add_spacer(height=10)

    with dpg.table(header_row=False, borders_innerV=False):
        dpg.add_table_column(width_fixed=True, init_width_or_weight=130)
        dpg.add_table_column()

        with dpg.table_row():
            dpg.add_text("Versão",   color=DIM)
            dpg.add_text(APP_VERSION, color=WHITE)
        with dpg.table_row():
            dpg.add_text("Empresa",  color=DIM)
            dpg.add_text(COMPANY_NAME, color=PURPLE)
        with dpg.table_row():
            dpg.add_text("Plataforma", color=DIM)
            dpg.add_text("Windows  /  Python 3.13  /  DearPyGui 2.x", color=WHITE)
        with dpg.table_row():
            dpg.add_text("Análise",  color=DIM)
            dpg.add_text("librosa  /  scikit-learn (Random Forest)", color=WHITE)
        with dpg.table_row():
            dpg.add_text("APIs",     color=DIM)
            dpg.add_text("Last.fm  /  Spotify  /  Discogs", color=WHITE)

    dpg.add_spacer(height=16)
    dpg.add_separator()
    dpg.add_spacer(height=10)
    dpg.add_text(APP_COPYRIGHT, color=DIM)
    dpg.add_spacer(height=6)
    dpg.add_text(
        "Este software é propriedade exclusiva da Techbak Solutions.\n"
        "Uso não autorizado é proibido.",
        color=DIM,
    )


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
        dpg.add_text("Music Analyzer", color=ACCENT)
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
                        dpg.add_text("Comparação de Faixas", color=ACCENT)
                        dpg.add_text(
                            "Carregue Faixa A na aba Analisar e Faixa B aqui.",
                            color=DIM,
                        )
                        W["compare_titles"] = dpg.add_text("", color=DIM)
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

            # ── Baixar ──────────────────────────────────────────────
            with dpg.tab(label="  Baixar  "):
                with dpg.child_window(border=False):
                    _build_download_tab()

            # ── Treinar ─────────────────────────────────────────────
            with dpg.tab(label="  Treinar  "):
                with dpg.child_window(border=False):
                    _build_train_tab()

            # ── Sobre ────────────────────────────────────────────────
            with dpg.tab(label="  Sobre  "):
                with dpg.child_window(border=False):
                    _build_about_tab()

    _build_splash()

    dpg.create_viewport(title=f"{APP_NAME}  |  {COMPANY_NAME}", width=1280, height=800,
                        min_width=960, min_height=620)
    dpg.setup_dearpygui()
    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()

    _splash_frames = [0]
    while dpg.is_dearpygui_running():
        try:
            _process_ui_queue()
        except Exception:
            traceback.print_exc()
        if dpg.does_item_exist("splash_window"):
            _splash_frames[0] += 1
            if _splash_frames[0] >= 240:  # ~4 segundos a 60fps
                dpg.delete_item("splash_window")
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    launch()
