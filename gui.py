"""
Music Analyzer — Interface Gráfica (DearPyGui 2.x)
Uso: python gui.py  ou  python main.py --gui
"""
import os
import threading
import queue
import ctypes
from ctypes import wintypes
import numpy as np
import dearpygui.dearpygui as dpg

from analyzer import analyze_file
from classifier import classify

# ── Fila thread-safe ─────────────────────────────────────────────────
_ui_queue: queue.Queue = queue.Queue()

def _ui(fn, *args, **kwargs):
    _ui_queue.put((fn, args, kwargs))

def _process_ui_queue():
    try:
        while True:
            fn, args, kwargs = _ui_queue.get_nowait()
            fn(*args, **kwargs)
    except queue.Empty:
        pass

# ── IDs dos widgets (preenchidos em _build_*) ─────────────────────────
W = {}  # chave → item ID inteiro

# ── Estado ───────────────────────────────────────────────────────────
_tracks = {"A": None, "B": None}

# ── Paleta ───────────────────────────────────────────────────────────
ACCENT  = (0,   229, 255, 255)
ACCENT2 = (255,  64, 129, 255)
SUCCESS = (105, 255,  71, 255)
DIM     = (100, 100, 100, 255)
WHITE   = (224, 224, 224, 255)
RED     = (255,  82,  82, 255)
YELLOW  = (255, 183,  77, 255)
GREEN   = (102, 187, 106, 255)
BLUE    = (100, 181, 246, 255)
PURPLE  = (179, 157, 219, 255)

def _fmt_dur(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"


# ── Diálogo Windows nativo ───────────────────────────────────────────
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


# ── Análise ───────────────────────────────────────────────────────────
def _open_file(slot: str):
    def _pick():
        path = _win_open_file(f"Selecionar faixa {slot}")
        if path:
            _ui(dpg.set_value, W[f"file_{slot}"], os.path.basename(path))
            _ui(dpg.set_value, W[f"status_{slot}"], "Analisando...")
            threading.Thread(target=_analyze, args=(slot, path), daemon=True).start()
    threading.Thread(target=_pick, daemon=True).start()


def _analyze(slot: str, path: str):
    try:
        f = analyze_file(path)
        c = classify(f)
        _tracks[slot] = {"features": f, "classification": c, "path": path}
        _ui(_apply_results, slot, f, c)
        _ui(_apply_spectrum, slot, f)
        _ui(_check_compare_ready)
    except Exception as e:
        import traceback; traceback.print_exc()
        _ui(dpg.set_value, W[f"status_{slot}"], f"Erro: {e}")


def _apply_results(slot: str, f: dict, c: dict):
    dpg.set_value(W[f"status_{slot}"],   "Concluído ✓")
    dpg.set_value(W[f"bpm_{slot}"],      f"{f['bpm']:.1f}")
    dpg.set_value(W[f"tom_{slot}"],      f["dominant_key"])
    dpg.set_value(W[f"dur_{slot}"],      _fmt_dur(f["duration_seconds"]))
    dpg.set_value(W[f"genero_{slot}"],   c["genre"])
    dpg.set_value(W[f"sub_{slot}"],      c.get("subgenre") or "—")
    dpg.set_value(W[f"conf_{slot}"],     f"{c['confidence']:.0%}  ({c.get('method','—')})")

    candidates = c.get("candidates", [])[:5]
    for i in range(5):
        if i < len(candidates):
            cand = candidates[i]
            txt = f"  {i+1}. {cand['genre']} / {cand.get('subgenre','—')}  —  {cand['score']:.0%}  ({cand.get('bpm_range','—')} BPM)"
        else:
            txt = ""
        dpg.set_value(W[f"cand_{slot}_{i}"], txt)


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
        dpg.set_value(W[f"bar_{slot}_{i}"],     ratio)
        dpg.set_value(W[f"barlbl_{slot}_{i}"],  f"{label}  {ratio:.0%}")

    rng     = np.random.default_rng(seed=42)
    samples = list(np.clip(rng.normal(f.get("rms_mean", 0.01),
                                      f.get("rms_std", 0.005) * 0.5, 80), 0, None))
    dpg.set_value(W[f"wave_{slot}"], {"x": list(range(len(samples))), "y": samples})


def _check_compare_ready():
    ready = _tracks["A"] is not None and _tracks["B"] is not None
    dpg.configure_item(W["btn_compare"], enabled=ready)


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

    rows = [
        ("BPM",         f"{fa['bpm']:.1f}",                     f"{fb['bpm']:.1f}",                     diff(fa["bpm"],fb["bpm"])),
        ("Tom",         fa["dominant_key"],                      fb["dominant_key"],                      "—"),
        ("Duração",     _fmt_dur(fa["duration_seconds"]),        _fmt_dur(fb["duration_seconds"]),        "—"),
        ("Gênero",      ca["genre"],                             cb["genre"],                             "—"),
        ("Subgênero",   ca.get("subgenre") or "—",               cb.get("subgenre") or "—",               "—"),
        ("Confiança",   f"{ca['confidence']:.0%}",               f"{cb['confidence']:.0%}",               "—"),
        ("Graves",      f"{fa['bass_ratio']:.0%}",               f"{fb['bass_ratio']:.0%}",               diff(fa["bass_ratio"],fb["bass_ratio"],".1%")),
        ("Brilho",      f"{fa['spectral_centroid_mean']:.0f}Hz", f"{fb['spectral_centroid_mean']:.0f}Hz", diff(fa["spectral_centroid_mean"],fb["spectral_centroid_mean"],".0f","Hz")),
        ("Energia RMS", f"{fa['rms_mean']:.4f}",                 f"{fb['rms_mean']:.4f}",                 diff(fa["rms_mean"],fb["rms_mean"],".4f")),
        ("Percussão",   f"{fa['percussive_ratio']:.2f}",         f"{fb['percussive_ratio']:.2f}",         diff(fa["percussive_ratio"],fb["percussive_ratio"],".2f")),
    ]
    for i, (label, v1, v2, d) in enumerate(rows):
        dpg.set_value(W[f"cmp_lbl_{i}"], label)
        dpg.set_value(W[f"cmp_a_{i}"],   v1)
        dpg.set_value(W[f"cmp_b_{i}"],   v2)
        dpg.set_value(W[f"cmp_d_{i}"],   d)

    dpg.set_value(W["compare_titles"],
                  f"{fa['file_name'][:30]}  vs  {fb['file_name'][:30]}")


# ── Construtores UI (guardam IDs em W) ───────────────────────────────
def _build_info_panel(slot: str, label: str, color):
    dpg.add_text(label, color=list(color))
    W[f"file_{slot}"]   = dpg.add_text("Nenhum arquivo", color=list(DIM), wrap=280)
    W[f"status_{slot}"] = dpg.add_text("")
    dpg.add_button(label="  Abrir arquivo  ", callback=lambda s=slot: _open_file(s))
    dpg.add_separator()

    for key, wkey in [("BPM","bpm"), ("Tom","tom"), ("Duração","dur"),
                      ("Gênero","genero"), ("Subgênero","sub"), ("Confiança","conf")]:
        with dpg.group(horizontal=True):
            dpg.add_text(f"{key}:", color=list(DIM))
            W[f"{wkey}_{slot}"] = dpg.add_text("—")

    dpg.add_spacer(height=6)
    dpg.add_text("Top candidatos:", color=list(DIM))
    for i in range(5):
        W[f"cand_{slot}_{i}"] = dpg.add_text("", color=list(WHITE), wrap=280)


def _build_spectrum(slot: str, color):
    dpg.add_text("Espectro de Frequências", color=list(color))
    dpg.add_separator()

    bar_colors = [RED, YELLOW, GREEN, ACCENT, BLUE, PURPLE]
    for i, bar_color in enumerate(bar_colors):
        W[f"barlbl_{slot}_{i}"] = dpg.add_text("—", color=list(bar_color))
        W[f"bar_{slot}_{i}"]    = dpg.add_progress_bar(default_value=0.0, width=-1)
        dpg.add_spacer(height=2)

    dpg.add_spacer(height=8)
    dpg.add_text("Waveform", color=list(DIM))
    with dpg.plot(height=90, width=-1, no_title=True, no_mouse_pos=True):
        dpg.add_plot_axis(dpg.mvXAxis, no_gridlines=True, no_tick_marks=True, no_tick_labels=True)
        with dpg.plot_axis(dpg.mvYAxis, no_gridlines=True, no_tick_marks=True, no_tick_labels=True):
            W[f"wave_{slot}"] = dpg.add_line_series([], [])


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


# ── Launch ────────────────────────────────────────────────────────────
def launch():
    dpg.create_context()

    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,      (15,  15,  15,  255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,       (26,  26,  26,  255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,       (37,  37,  37,  255))
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (50,  50,  50,  255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (0,  180, 200, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (0,  229, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,          (224, 224, 224, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (0,   80, 100, 255))
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,  4)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,    8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,  12, 12)
    dpg.bind_theme(global_theme)

    with dpg.window(tag="main_window", no_title_bar=True, no_move=True, no_resize=True):
        dpg.add_text("Music Analyzer", color=list(ACCENT))
        dpg.add_separator()

        with dpg.tab_bar():
            with dpg.tab(label="  Analisar  "):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=300, border=True):
                        _build_info_panel("A", "Faixa", ACCENT)
                    with dpg.child_window(border=True):
                        _build_spectrum("A", ACCENT)

            with dpg.tab(label="  Comparar  "):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=300, border=True):
                        _build_info_panel("B", "Faixa B", ACCENT2)
                        dpg.add_separator()
                        _build_spectrum("B", ACCENT2)

                    with dpg.child_window(border=True):
                        dpg.add_text("Comparação de Faixas", color=list(ACCENT))
                        W["compare_titles"] = dpg.add_text("", color=list(DIM))
                        W["btn_compare"] = dpg.add_button(
                            label="  Comparar agora  ",
                            callback=_run_compare,
                            enabled=False,
                        )
                        dpg.add_separator()
                        _build_compare_table()

    dpg.create_viewport(title="Music Analyzer", width=1200, height=780,
                        min_width=900, min_height=600)
    dpg.setup_dearpygui()
    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()

    while dpg.is_dearpygui_running():
        try:
            _process_ui_queue()
        except Exception as e:
            import traceback; traceback.print_exc()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    launch()