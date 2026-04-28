# pylint: disable=invalid-name  # module name uses digits per project convention
"""
╔══════════════════════════════════════════════════════════════╗
║         360° IMAGE COMPRESSION OPTIMIZER                     ║
║         Professional Tool for Virtual Tour Assets            ║
╚══════════════════════════════════════════════════════════════╝

Finds the optimal compression point for 360 panoramic images.
Target: Reduce from ~15MB to ~5MB while preserving visual quality.

Usage:
    python 360_optimizer.py [image_path]

Requirements:
    pip install Pillow numpy scikit-image
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import io
import os
import sys
import math
import time
from pathlib import Path
from typing import Optional, Callable

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Pillow not found. Install with: pip install Pillow")
    sys.exit(1)

# Pillow >=10 moved resampling filters to Image.Resampling.*
# This shim supports both old and new versions.
LANCZOS = getattr(Image, "Resampling", Image).LANCZOS

try:
    import numpy as np
except ImportError:
    print("NumPy not found. Install with: pip install numpy")
    sys.exit(1)

# Optional: scikit-image for SSIM metric
try:
    from skimage.metrics import structural_similarity as ssim_metric
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


# ─────────────────────────────────────────────────────────────
#  CONSTANTS & THEME
# ─────────────────────────────────────────────────────────────
DARK_BG        = "#0D1117"
PANEL_BG       = "#161B22"
CARD_BG        = "#1C2128"
BORDER_COLOR   = "#30363D"
ACCENT_BLUE    = "#58A6FF"
ACCENT_GREEN   = "#3FB950"
ACCENT_ORANGE  = "#D29922"
ACCENT_RED     = "#F85149"
TEXT_PRIMARY   = "#E6EDF3"
TEXT_SECONDARY = "#8B949E"
TEXT_MUTED     = "#484F58"

FONT_MONO  = ("Courier New", 10)
FONT_LABEL = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_STAT  = ("Courier New", 18, "bold")

PREVIEW_W  = 640
PREVIEW_H  = 320
THUMB_SIZE = (PREVIEW_W, PREVIEW_H)


# ─────────────────────────────────────────────────────────────
#  COMPRESSION ENGINE
# ─────────────────────────────────────────────────────────────
class CompressionEngine:
    """Handles all image compression logic and quality metrics."""

    def __init__(self) -> None:
        """Initialize with empty state."""
        self.original_image: Optional[Image.Image] = None
        self.original_path: str = ""
        self.original_bytes: int = 0

    def load_image(self, path: str) -> bool:
        """Load an image from disk and normalise to RGB."""
        try:
            # 360 images are commonly >89MP — disable the decompression bomb guard
            Image.MAX_IMAGE_PIXELS = None
            img = Image.open(path)
            if img.mode == "RGBA":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            self.original_image = img
            self.original_path = path
            self.original_bytes = os.path.getsize(path)
            return True
        except (OSError, ValueError) as exc:
            print(f"Load error: {exc}")
            return False

    def compress(
        self,
        quality: int,
        fmt: str = "JPEG",
        progressive: bool = True,
        subsampling: int = 0,
    ) -> dict:
        """Compress the loaded image and return metrics dict.

        Returns keys: bytes_io, size_bytes, size_mb, ratio,
                      savings_pct, psnr, ssim, encode_ms, image
        """
        if self.original_image is None:
            return {}

        t0 = time.perf_counter()
        buf = io.BytesIO()

        if fmt == "JPEG":
            save_kwargs: dict = {
                "format": "JPEG",
                "quality": quality,
                "optimize": True,
                "progressive": progressive,
                "subsampling": subsampling,
            }
        else:
            save_kwargs = {
                "format": "WEBP",
                "quality": quality,
                "method": 6,
            }

        self.original_image.save(buf, **save_kwargs)
        encode_ms = (time.perf_counter() - t0) * 1000
        size = buf.tell()

        buf.seek(0)
        compressed_img = Image.open(buf)
        compressed_img.load()

        # Metrics are best-effort — a failure must never block the export
        try:
            psnr_val = self._psnr(self.original_image, compressed_img)
        except Exception:  # pylint: disable=broad-except
            psnr_val = 0.0
        try:
            ssim_val = self._ssim(self.original_image, compressed_img)
        except Exception:  # pylint: disable=broad-except
            ssim_val = -1.0

        buf.seek(0)
        savings = (
            (1 - size / self.original_bytes) * 100
            if self.original_bytes > 0
            else 0
        )
        return {
            "bytes_io":    buf,
            "size_bytes":  size,
            "size_mb":     size / 1_048_576,
            "ratio":       self.original_bytes / size if size > 0 else 0,
            "savings_pct": savings,
            "psnr":        psnr_val,
            "ssim":        ssim_val,
            "encode_ms":   encode_ms,
            "image":       compressed_img,
        }

    def _psnr(self, img1: Image.Image, img2: Image.Image) -> float:
        """Return Peak Signal-to-Noise Ratio in dB (higher = better).

        Images are downsampled to max 2048px before computing to keep
        memory usage reasonable for large 360 panoramas (>89MP).
        PSNR is resolution-independent so results remain accurate.
        """
        try:
            # Downsample for memory safety — 2048px cap uses ~50 MB max
            scale = min(1.0, 2048 / max(img1.width, img1.height))
            dim_w = max(1, int(img1.width * scale))
            dim_h = max(1, int(img1.height * scale))
            r1 = img1.resize((dim_w, dim_h), LANCZOS).convert("RGB")
            r2 = img2.resize((dim_w, dim_h), LANCZOS).convert("RGB")
            a1 = np.array(r1, dtype=np.float32)
            a2 = np.array(r2, dtype=np.float32)
            mse = np.mean((a1 - a2) ** 2)
            if mse == 0:
                return 100.0
            return float(20 * math.log10(255.0 / math.sqrt(mse)))
        except (ValueError, ZeroDivisionError, MemoryError):
            return 0.0

    def _ssim(self, img1: Image.Image, img2: Image.Image) -> float:
        """Return Structural Similarity Index (1.0 = identical)."""
        if not HAS_SKIMAGE:
            return -1.0
        try:
            scale = min(1.0, 1024 / max(img1.width, img1.height))
            dim_w = int(img1.width * scale)
            dim_h = int(img1.height * scale)
            a1 = np.array(img1.resize((dim_w, dim_h), LANCZOS).convert("L"))
            a2 = np.array(img2.resize((dim_w, dim_h), LANCZOS).convert("L"))
            return float(ssim_metric(a1, a2, data_range=255))
        except (ValueError, RuntimeError):
            return -1.0

    def make_thumbnail(self, img: Image.Image) -> Image.Image:
        """Return a padded thumbnail of THUMB_SIZE from img."""
        img_copy = img.copy()
        img_copy.thumbnail(THUMB_SIZE, LANCZOS)
        thumb = Image.new("RGB", THUMB_SIZE, (13, 17, 23))
        x = (THUMB_SIZE[0] - img_copy.width) // 2
        y = (THUMB_SIZE[1] - img_copy.height) // 2
        thumb.paste(img_copy, (x, y))
        return thumb


# ─────────────────────────────────────────────────────────────
#  QUALITY METER CANVAS
# ─────────────────────────────────────────────────────────────
class QualityMeterBar(tk.Canvas):
    """Gradient slider canvas mapping position to JPEG quality (1–100)."""

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        """Initialise canvas with event bindings."""
        super().__init__(parent, bg=DARK_BG, highlightthickness=0, **kwargs)
        self.quality: int = 75
        self.dragging: bool = False
        self._callback: Optional[Callable[[int], None]] = None
        self.bind("<Configure>", self._on_resize)
        self.bind("<ButtonPress-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_scroll)

    def set_callback(self, fn: Callable[[int], None]) -> None:
        """Register a callback invoked with the new quality int on change."""
        self._callback = fn

    def set_quality(self, q: int) -> None:
        """Set quality value and redraw."""
        self.quality = max(1, min(100, q))
        self._draw()

    def _pos_to_quality(self, x: int) -> int:
        """Convert canvas x-coordinate to quality value."""
        margin = 20
        bar_w = self.winfo_width() - margin * 2
        ratio = (x - margin) / bar_w if bar_w > 0 else 0
        return max(1, min(100, int(ratio * 99) + 1))

    def _on_resize(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        """Redraw when widget is resized."""
        self._draw()

    def _on_click(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle mouse button press."""
        self.dragging = True
        self.set_quality(self._pos_to_quality(event.x))
        if self._callback:
            self._callback(self.quality)

    def _on_drag(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle mouse drag."""
        if self.dragging:
            self.set_quality(self._pos_to_quality(event.x))
            if self._callback:
                self._callback(self.quality)

    def _on_release(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle mouse button release."""
        self.dragging = False

    def _on_scroll(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle mouse wheel to nudge quality by ±1."""
        delta = 1 if event.delta > 0 else -1
        self.set_quality(self.quality + delta)
        if self._callback:
            self._callback(self.quality)

    def _draw(self) -> None:
        """Render the gradient bar, thumb, and labels."""
        self.delete("all")
        canvas_w = self.winfo_width()
        canvas_h = self.winfo_height()
        if canvas_w < 10 or canvas_h < 10:
            return

        margin = 20
        bar_y = canvas_h // 2
        bar_h = 12
        radius = bar_h // 2
        track_w = canvas_w - margin * 2

        # Gradient track: red → orange → green
        steps = 120
        for i in range(steps):
            x0 = margin + int(i / steps * track_w)
            x1 = margin + int((i + 1) / steps * track_w)
            ratio = i / steps
            if ratio < 0.33:
                seg_r = 248
                seg_g = int(ratio / 0.33 * 130 + 83)
                seg_b = 73
            elif ratio < 0.66:
                seg_r = int(248 - (ratio - 0.33) / 0.33 * 200)
                seg_g = int(213 - (ratio - 0.33) / 0.33 * 50)
                seg_b = 73
            else:
                seg_r = 48
                seg_g = int(163 + (ratio - 0.66) / 0.34 * 20)
                seg_b = 80
            color = f"#{seg_r:02x}{seg_g:02x}{seg_b:02x}"
            self.create_rectangle(
                x0, bar_y - radius, x1, bar_y + radius,
                fill=color, outline=""
            )

        self.create_rectangle(
            margin, bar_y - radius,
            canvas_w - margin, bar_y + radius,
            fill="", outline=BORDER_COLOR, width=1,
        )

        # Position marker & thumb
        px = margin + int((self.quality - 1) / 99 * track_w)
        self.create_line(
            px, bar_y - radius - 8,
            px, bar_y + radius + 8,
            fill=TEXT_PRIMARY, width=2,
        )
        thumb_r = 10
        self.create_oval(
            px - thumb_r, bar_y - thumb_r,
            px + thumb_r, bar_y + thumb_r,
            fill=DARK_BG, outline=ACCENT_BLUE, width=2,
        )
        self.create_text(
            px, bar_y,
            text=str(self.quality),
            fill=ACCENT_BLUE,
            font=("Courier New", 7, "bold"),
        )

        # Zone labels
        label_y = bar_y + radius + 16
        self.create_text(
            margin, label_y, text="● MAX COMPRESS",
            anchor="w", fill=ACCENT_RED, font=("Segoe UI", 8),
        )
        self.create_text(
            canvas_w // 2, label_y, text="◆ BALANCED ZONE",
            fill=ACCENT_ORANGE, font=("Segoe UI", 8),
        )
        self.create_text(
            canvas_w - margin, label_y, text="MAX QUALITY ●",
            anchor="e", fill=ACCENT_GREEN, font=("Segoe UI", 8),
        )

        # Tick marks at Q25, Q50, Q75
        for tick in (25, 50, 75):
            tx = margin + int((tick - 1) / 99 * track_w)
            self.create_line(
                tx, bar_y - radius - 4,
                tx, bar_y - radius,
                fill=TEXT_MUTED, width=1,
            )
            self.create_text(
                tx, bar_y - radius - 12,
                text=str(tick),
                fill=TEXT_MUTED, font=("Segoe UI", 7),
            )


# ─────────────────────────────────────────────────────────────
#  STAT CARD WIDGET
# ─────────────────────────────────────────────────────────────
class StatCard(tk.Frame):
    """Compact metric display card with a large numeric value."""

    def __init__(
        self, parent: tk.Widget, label: str, unit: str = "", **kwargs
    ) -> None:
        """Build the card layout."""
        super().__init__(parent, bg=CARD_BG, **kwargs)
        self.configure(relief="flat", bd=0)

        tk.Label(
            self, text=label, bg=CARD_BG, fg=TEXT_SECONDARY, font=FONT_SMALL
        ).pack(pady=(10, 2))

        self._val_var = tk.StringVar(value="—")
        self._val_label = tk.Label(
            self, textvariable=self._val_var,
            bg=CARD_BG, fg=TEXT_PRIMARY, font=FONT_STAT,
        )
        self._val_label.pack()

        if unit:
            tk.Label(
                self, text=unit, bg=CARD_BG, fg=TEXT_MUTED, font=FONT_SMALL
            ).pack(pady=(0, 10))
        else:
            tk.Frame(self, height=10, bg=CARD_BG).pack()

    def set(self, value: str, color: str = TEXT_PRIMARY) -> None:
        """Update displayed value and its colour."""
        self._val_var.set(value)
        self._val_label.configure(fg=color)


# ─────────────────────────────────────────────────────────────
#  MAIN APPLICATION
# ─────────────────────────────────────────────────────────────
class App360Optimizer(tk.Tk):
    """Main application window for the 360° compression optimizer."""

    def __init__(self) -> None:
        """Create the main window and initialise all state."""
        super().__init__()
        self.title(
            "360° Image Compression Optimizer  —  Virtual Tour Asset Tool"
        )
        self.configure(bg=DARK_BG)
        self.minsize(1200, 820)

        self.engine = CompressionEngine()
        self._job: Optional[str] = None
        self._computing: bool = False

        self._quality    = tk.IntVar(value=75)
        self._format     = tk.StringVar(value="JPEG")
        self._prog       = tk.BooleanVar(value=True)
        self._subsample  = tk.IntVar(value=2)
        self._batch_dir  = tk.StringVar(value="")

        # Widgets declared here to satisfy W0201
        self._lbl_filename: Optional[tk.Label] = None
        self._lbl_filesize: Optional[tk.Label] = None
        self._lbl_dimensions: Optional[tk.Label] = None
        self._quality_entry: Optional[tk.Entry] = None
        self._progress: Optional[ttk.Progressbar] = None
        self._lbl_batch_status: Optional[tk.Label] = None
        self.stat_original: Optional[StatCard] = None
        self.stat_compressed: Optional[StatCard] = None
        self.stat_savings: Optional[StatCard] = None
        self.stat_psnr: Optional[StatCard] = None
        self.stat_ssim: Optional[StatCard] = None
        self.stat_ratio: Optional[StatCard] = None
        self._canvas_orig: Optional[tk.Canvas] = None
        self._comp_label_var: Optional[tk.StringVar] = None
        self._canvas_comp: Optional[tk.Canvas] = None
        self._slider_bar: Optional[QualityMeterBar] = None
        self._status_var: Optional[tk.StringVar] = None
        self._computing_label: Optional[tk.Label] = None
        self._photo_orig: Optional[ImageTk.PhotoImage] = None
        self._photo_comp: Optional[ImageTk.PhotoImage] = None

        self._build_ui()
        self._update_empty_state()

        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self.after(200, lambda: self._load_image(sys.argv[1]))

    # ── BUILD UI ────────────────────────────────────────────
    def _build_ui(self) -> None:
        """Construct all UI sections."""
        self._build_topbar()
        main = tk.Frame(self, bg=DARK_BG)
        main.pack(fill="both", expand=True)

        left = tk.Frame(main, bg=PANEL_BG, width=280)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._build_left_panel(left)

        right = tk.Frame(main, bg=DARK_BG)
        right.pack(side="left", fill="both", expand=True)
        self._build_right_panel(right)

    def _build_topbar(self) -> None:
        """Build the top navigation bar."""
        topbar = tk.Frame(self, bg=PANEL_BG, height=56)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        tk.Label(
            topbar, text="360°", bg=PANEL_BG, fg=ACCENT_BLUE,
            font=("Courier New", 22, "bold"),
        ).pack(side="left", padx=20, pady=8)
        tk.Label(
            topbar, text="COMPRESSION OPTIMIZER",
            bg=PANEL_BG, fg=TEXT_PRIMARY,
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left", pady=8)
        tk.Label(
            topbar, text="Virtual Tour Asset Tool  •  FVG UNESCO",
            bg=PANEL_BG, fg=TEXT_MUTED, font=FONT_SMALL,
        ).pack(side="left", padx=16, pady=8)

        ssim_info = (
            "SSIM enabled" if HAS_SKIMAGE
            else "pip install scikit-image for SSIM"
        )
        ssim_color = ACCENT_GREEN if HAS_SKIMAGE else ACCENT_ORANGE
        tk.Label(
            topbar, text=ssim_info, bg=PANEL_BG,
            fg=ssim_color, font=FONT_SMALL,
        ).pack(side="right", padx=20)

        tk.Button(
            topbar, text="  \U0001f4c2  OPEN IMAGE",
            bg=ACCENT_BLUE, fg=DARK_BG,
            font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", padx=14,
            command=self._open_file,
        ).pack(side="right", padx=8, pady=10)

    def _build_left_panel(self, parent: tk.Frame) -> None:
        """Build the left control panel."""
        self._section(parent, "SOURCE FILE")
        self._lbl_filename = tk.Label(
            parent, text="No file loaded",
            bg=PANEL_BG, fg=TEXT_PRIMARY,
            font=FONT_SMALL, wraplength=240, justify="left",
        )
        self._lbl_filename.pack(anchor="w", padx=16, pady=4)
        self._lbl_filesize = tk.Label(
            parent, text="", bg=PANEL_BG,
            fg=TEXT_SECONDARY, font=FONT_SMALL,
        )
        self._lbl_filesize.pack(anchor="w", padx=16)
        self._lbl_dimensions = tk.Label(
            parent, text="", bg=PANEL_BG,
            fg=TEXT_SECONDARY, font=FONT_SMALL,
        )
        self._lbl_dimensions.pack(anchor="w", padx=16, pady=(0, 8))

        self._section(parent, "OUTPUT FORMAT")
        for fmt in ("JPEG", "WebP"):
            tk.Radiobutton(
                parent, text=fmt, variable=self._format, value=fmt,
                bg=PANEL_BG, fg=TEXT_PRIMARY, selectcolor=DARK_BG,
                activebackground=PANEL_BG, font=FONT_LABEL,
                cursor="hand2", command=self._on_param_change,
            ).pack(anchor="w", padx=16)

        self._section(parent, "JPEG OPTIONS")
        tk.Checkbutton(
            parent, text="Progressive JPEG",
            variable=self._prog, bg=PANEL_BG, fg=TEXT_PRIMARY,
            selectcolor=DARK_BG, activebackground=PANEL_BG,
            font=FONT_LABEL, cursor="hand2",
            command=self._on_param_change,
        ).pack(anchor="w", padx=16, pady=2)

        tk.Label(
            parent, text="Chroma Subsampling:",
            bg=PANEL_BG, fg=TEXT_SECONDARY, font=FONT_SMALL,
        ).pack(anchor="w", padx=16, pady=(8, 2))
        for text, val in [
            ("4:4:4 (best color)", 0),
            ("4:2:2 (balanced)", 1),
            ("4:2:0 (smallest)", 2),
        ]:
            tk.Radiobutton(
                parent, text=text, variable=self._subsample, value=val,
                bg=PANEL_BG, fg=TEXT_PRIMARY, selectcolor=DARK_BG,
                activebackground=PANEL_BG, font=FONT_SMALL,
                cursor="hand2", command=self._on_param_change,
            ).pack(anchor="w", padx=24)
        tk.Frame(parent, height=8, bg=PANEL_BG).pack()

        self._section(parent, "QUALITY VALUE")
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(padx=16, pady=8, fill="x")
        tk.Label(
            row, text="Quality:", bg=PANEL_BG,
            fg=TEXT_SECONDARY, font=FONT_LABEL,
        ).pack(side="left")
        self._quality_entry = tk.Entry(
            row, textvariable=self._quality, width=5,
            bg=CARD_BG, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY, relief="flat",
            font=FONT_MONO, justify="center",
        )
        self._quality_entry.pack(side="left", padx=8)
        self._quality_entry.bind(
            "<Return>", lambda _e: self._on_entry_change()
        )
        tk.Label(
            row, text="/ 100", bg=PANEL_BG,
            fg=TEXT_MUTED, font=FONT_SMALL,
        ).pack(side="left")

        self._section(parent, "TARGET SIZE PRESETS")
        for label, preset_q in [
            ("\U0001f3af  ~5MB target", 65),
            ("\u26a1  ~3MB fast load", 50),
            ("\U0001f3c6  Max quality", 92),
        ]:
            tk.Button(
                parent, text=label, bg=CARD_BG, fg=TEXT_PRIMARY,
                font=FONT_SMALL, relief="flat", cursor="hand2",
                padx=8, pady=4,
                command=lambda q=preset_q: self._set_quality(q),
            ).pack(fill="x", padx=16, pady=2)

        self._section(parent, "EXPORT")
        tk.Button(
            parent, text="\U0001f4be  SAVE COMPRESSED IMAGE",
            bg=ACCENT_GREEN, fg=DARK_BG,
            font=("Segoe UI", 10, "bold"), relief="flat",
            cursor="hand2", pady=8,
            command=self._export_single,
        ).pack(fill="x", padx=16, pady=4)

        tk.Label(
            parent, text="Batch — folder:",
            bg=PANEL_BG, fg=TEXT_SECONDARY, font=FONT_SMALL,
        ).pack(anchor="w", padx=16, pady=(8, 0))
        batch_row = tk.Frame(parent, bg=PANEL_BG)
        batch_row.pack(fill="x", padx=16, pady=2)
        tk.Entry(
            batch_row, textvariable=self._batch_dir, bg=CARD_BG,
            fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            relief="flat", font=FONT_SMALL,
        ).pack(side="left", fill="x", expand=True)
        tk.Button(
            batch_row, text="\u2026", bg=CARD_BG, fg=TEXT_PRIMARY,
            relief="flat", cursor="hand2", font=FONT_LABEL,
            command=self._pick_batch_dir,
        ).pack(side="left", padx=(4, 0))

        tk.Button(
            parent, text="\U0001f680  BATCH COMPRESS FOLDER",
            bg=ACCENT_ORANGE, fg=DARK_BG,
            font=("Segoe UI", 10, "bold"), relief="flat",
            cursor="hand2", pady=8,
            command=self._batch_compress,
        ).pack(fill="x", padx=16, pady=4)

        self._progress = ttk.Progressbar(
            parent, mode="determinate", length=240, maximum=100
        )
        self._progress.pack(padx=16, pady=4)
        self._lbl_batch_status = tk.Label(
            parent, text="", bg=PANEL_BG,
            fg=TEXT_SECONDARY, font=FONT_SMALL, wraplength=240,
        )
        self._lbl_batch_status.pack(padx=16)

    def _build_right_panel(self, parent: tk.Frame) -> None:
        """Build the right preview and metrics panel."""
        # Stats row
        stats_row = tk.Frame(parent, bg=DARK_BG)
        stats_row.pack(fill="x", padx=16, pady=(16, 8))

        self.stat_original   = StatCard(stats_row, "ORIGINAL SIZE", "MB")
        self.stat_compressed = StatCard(stats_row, "COMPRESSED SIZE", "MB")
        self.stat_savings    = StatCard(stats_row, "SIZE SAVINGS", "%")
        self.stat_psnr       = StatCard(stats_row, "PSNR", "dB")
        self.stat_ssim       = StatCard(stats_row, "SSIM", "score")
        self.stat_ratio      = StatCard(stats_row, "RATIO", "x")

        for card in (
            self.stat_original, self.stat_compressed,
            self.stat_savings, self.stat_psnr,
            self.stat_ssim, self.stat_ratio,
        ):
            card.pack(side="left", fill="both", expand=True, padx=4, ipady=4)

        # Previews
        preview_frame = tk.Frame(parent, bg=DARK_BG)
        preview_frame.pack(fill="both", expand=True, padx=16)

        orig_box = tk.Frame(preview_frame, bg=CARD_BG)
        orig_box.pack(side="left", fill="both", expand=True, padx=(0, 4))
        tk.Label(
            orig_box, text="ORIGINAL", bg=CARD_BG,
            fg=TEXT_MUTED, font=("Segoe UI", 9, "bold"),
        ).pack(pady=(8, 4))
        self._canvas_orig = tk.Canvas(
            orig_box, bg="#0a0e13",
            width=PREVIEW_W, height=PREVIEW_H,
            highlightthickness=1, highlightbackground=BORDER_COLOR,
        )
        self._canvas_orig.pack(padx=8, pady=(0, 8))

        comp_box = tk.Frame(preview_frame, bg=CARD_BG)
        comp_box.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self._comp_label_var = tk.StringVar(value="COMPRESSED  (Q75)")
        tk.Label(
            comp_box, textvariable=self._comp_label_var,
            bg=CARD_BG, fg=ACCENT_BLUE,
            font=("Segoe UI", 9, "bold"),
        ).pack(pady=(8, 4))
        self._canvas_comp = tk.Canvas(
            comp_box, bg="#0a0e13",
            width=PREVIEW_W, height=PREVIEW_H,
            highlightthickness=1, highlightbackground=BORDER_COLOR,
        )
        self._canvas_comp.pack(padx=8, pady=(0, 8))

        # Slider section
        slider_section = tk.Frame(parent, bg=PANEL_BG)
        slider_section.pack(fill="x", padx=16, pady=(8, 16))

        hdr = tk.Frame(slider_section, bg=PANEL_BG)
        hdr.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(
            hdr, text="\u27f5  HIGH COMPRESSION  /  LOW QUALITY",
            bg=PANEL_BG, fg=ACCENT_RED, font=FONT_SMALL,
        ).pack(side="left")
        tk.Label(
            hdr, text="DRAG TO SET QUALITY \u2192",
            bg=PANEL_BG, fg=TEXT_MUTED, font=FONT_SMALL,
        ).pack()
        tk.Label(
            hdr, text="LOW COMPRESSION  /  HIGH QUALITY  \u27f6",
            bg=PANEL_BG, fg=ACCENT_GREEN, font=FONT_SMALL,
        ).pack(side="right")

        self._slider_bar = QualityMeterBar(slider_section, height=70)
        self._slider_bar.pack(fill="x", padx=16, pady=(0, 12))
        self._slider_bar.set_callback(self._on_slider_change)
        self._slider_bar.set_quality(75)

        # Status bar
        self._status_var = tk.StringVar(
            value="Ready  —  Open a 360\u00b0 image to begin"
        )
        status = tk.Frame(parent, bg=PANEL_BG, height=28)
        status.pack(fill="x", side="bottom")
        tk.Label(
            status, textvariable=self._status_var,
            bg=PANEL_BG, fg=TEXT_MUTED, font=FONT_SMALL, anchor="w",
        ).pack(side="left", padx=16)

        self._computing_label = tk.Label(
            status, text="", bg=PANEL_BG, fg=ACCENT_ORANGE, font=FONT_SMALL
        )
        self._computing_label.pack(side="right", padx=16)

    # ── HELPERS ─────────────────────────────────────────────
    def _section(self, parent: tk.Widget, title: str) -> None:
        """Insert a titled divider into the left panel."""
        tk.Frame(parent, height=1, bg=BORDER_COLOR).pack(fill="x")
        tk.Label(
            parent, text=title, bg=PANEL_BG, fg=TEXT_MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=16, pady=(10, 4))

    def _set_quality(self, q: int) -> None:
        """Apply a preset quality value."""
        self._quality.set(q)
        if self._slider_bar:
            self._slider_bar.set_quality(q)
        self._schedule_update()

    def _on_slider_change(self, q: int) -> None:
        """Callback from slider drag."""
        self._quality.set(q)
        self._schedule_update()

    def _on_entry_change(self) -> None:
        """Callback from quality entry field."""
        try:
            q = int(self._quality.get())
            if self._slider_bar:
                self._slider_bar.set_quality(q)
            self._schedule_update()
        except ValueError:
            pass

    def _on_param_change(self) -> None:
        """Callback when format/subsampling options change."""
        self._schedule_update()

    def _schedule_update(self) -> None:
        """Debounce: wait 220 ms after last change before computing."""
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(220, self._run_compression)

    # ── OPEN & LOAD ─────────────────────────────────────────
    def _open_file(self) -> None:
        """Open a file dialog and load the selected image."""
        path = filedialog.askopenfilename(
            title="Open 360\u00b0 Image",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.webp *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._load_image(path)

    def _load_image(self, path: str) -> None:
        """Load image from path and refresh UI."""
        if self._status_var:
            self._status_var.set(
                f"Loading  {os.path.basename(path)} \u2026"
            )
        self.update()
        if not self.engine.load_image(path):
            messagebox.showerror("Error", f"Could not open:\n{path}")
            return

        name = os.path.basename(path)
        size_mb = self.engine.original_bytes / 1_048_576
        img_w, img_h = self.engine.original_image.size  # type: ignore[union-attr]

        if self._lbl_filename:
            self._lbl_filename.configure(text=name)
        if self._lbl_filesize:
            self._lbl_filesize.configure(
                text=(
                    f"Size: {size_mb:.2f} MB"
                    f"  ({self.engine.original_bytes:,} bytes)"
                )
            )
        if self._lbl_dimensions:
            self._lbl_dimensions.configure(
                text=(
                    f"Resolution: {img_w} \u00d7 {img_h}"
                    f"  ({img_w * img_h // 1_000_000:.1f}MP)"
                )
            )
        if self.stat_original:
            self.stat_original.set(f"{size_mb:.2f}", ACCENT_BLUE)

        thumb = self.engine.make_thumbnail(
            self.engine.original_image  # type: ignore[arg-type]
        )
        self._photo_orig = ImageTk.PhotoImage(thumb)
        if self._canvas_orig:
            self._canvas_orig.delete("all")
            self._canvas_orig.create_image(
                PREVIEW_W // 2, PREVIEW_H // 2,
                anchor="center", image=self._photo_orig,
            )

        if self._status_var:
            self._status_var.set(
                f"Loaded: {name}  |  {img_w}\u00d7{img_h}"
                f"  |  {size_mb:.2f} MB"
                f"  \u2014  Drag the slider to optimize"
            )
        self._schedule_update()

    # ── COMPRESSION UPDATE ───────────────────────────────────
    def _run_compression(self) -> None:
        """Start a background compression thread (debounced)."""
        if self.engine.original_image is None:
            return
        if self._computing:
            self._job = self.after(100, self._run_compression)
            return

        quality    = self._quality.get()
        fmt        = self._format.get()
        prog       = self._prog.get()
        sub        = self._subsample.get()

        self._computing = True
        if self._computing_label:
            self._computing_label.configure(text="\u27f3 computing\u2026")
        self.update_idletasks()

        def work() -> None:
            """Run compression off the main thread."""
            result = self.engine.compress(quality, fmt, prog, sub)
            self.after(0, lambda: self._apply_result(result, quality, fmt))

        threading.Thread(target=work, daemon=True).start()

    def _apply_result(self, result: dict, quality: int, fmt: str) -> None:
        """Apply compression result to the UI (called on main thread)."""
        self._computing = False
        if self._computing_label:
            self._computing_label.configure(text="")
        if not result:
            return

        size_mb   = result["size_mb"]
        savings   = result["savings_pct"]
        psnr_val  = result["psnr"]
        ssim_val  = result["ssim"]

        size_color = (
            ACCENT_GREEN if size_mb <= 5.0
            else ACCENT_ORANGE if size_mb <= 8.0
            else ACCENT_RED
        )
        if self.stat_compressed:
            self.stat_compressed.set(f"{size_mb:.2f}", size_color)
        if self.stat_savings:
            self.stat_savings.set(
                f"{savings:.1f}",
                ACCENT_GREEN if savings >= 50 else ACCENT_ORANGE,
            )
        if self.stat_psnr:
            self.stat_psnr.set(
                f"{psnr_val:.1f}",
                ACCENT_GREEN if psnr_val > 40
                else ACCENT_ORANGE if psnr_val > 35
                else ACCENT_RED,
            )
        if self.stat_ssim:
            if ssim_val >= 0:
                self.stat_ssim.set(
                    f"{ssim_val:.4f}",
                    ACCENT_GREEN if ssim_val > 0.97
                    else ACCENT_ORANGE if ssim_val > 0.93
                    else ACCENT_RED,
                )
            else:
                self.stat_ssim.set("N/A", TEXT_MUTED)
        if self.stat_ratio:
            self.stat_ratio.set(f"{result['ratio']:.1f}x", ACCENT_BLUE)

        thumb = self.engine.make_thumbnail(result["image"])
        self._photo_comp = ImageTk.PhotoImage(thumb)
        if self._canvas_comp:
            self._canvas_comp.delete("all")
            self._canvas_comp.create_image(
                PREVIEW_W // 2, PREVIEW_H // 2,
                anchor="center", image=self._photo_comp,
            )

        if self._comp_label_var:
            self._comp_label_var.set(
                f"COMPRESSED  ({fmt} Q{quality}"
                f"  \u2022  {size_mb:.2f} MB"
                f"  \u2022  encode {result['encode_ms']:.0f}ms)"
            )

        if self._status_var:
            self._status_var.set(
                self._quality_hint(psnr_val, ssim_val, size_mb)
            )
        if self._slider_bar:
            self._slider_bar.set_quality(quality)

    def _quality_hint(
        self, psnr: float, ssim_v: float, size_mb: float
    ) -> str:
        """Build a human-readable quality assessment string."""
        hints = []
        if psnr > 42:
            hints.append("\u2705 Excellent PSNR \u2014 imperceptible loss")
        elif psnr > 38:
            hints.append("\u2713 Good PSNR \u2014 minor artifacts possible")
        elif psnr > 34:
            hints.append(
                "\u26a0 Moderate PSNR \u2014 noticeable in close-up"
            )
        else:
            hints.append(
                "\u2717 Low PSNR \u2014 visible compression artifacts"
            )
        if ssim_v >= 0:
            if ssim_v > 0.97:
                hints.append("SSIM excellent")
            elif ssim_v > 0.93:
                hints.append("SSIM good")
            else:
                hints.append("SSIM degraded")
        if size_mb <= 5:
            hints.append(
                f"\U0001f3af {size_mb:.2f} MB \u2014 within 5MB target!"
            )
        else:
            hints.append(
                f"\U0001f4e6 {size_mb:.2f} MB"
                f" \u2014 reduce quality to hit 5MB target"
            )
        return "  |  ".join(hints)

    # ── EXPORT ──────────────────────────────────────────────
    def _export_single(self) -> None:
        """Save the currently compressed image to disk."""
        if self.engine.original_image is None:
            messagebox.showwarning("No Image", "Open an image first.")
            return

        quality = self._quality.get()
        fmt     = self._format.get()
        ext     = "jpg" if fmt == "JPEG" else "webp"
        default = (
            Path(self.engine.original_path).stem + f"_q{quality}.{ext}"
        )
        path = filedialog.asksaveasfilename(
            initialfile=default,
            defaultextension=f".{ext}",
            filetypes=[(fmt, f"*.{ext}"), ("All files", "*.*")],
        )
        if not path:
            return

        result = self.engine.compress(
            quality, fmt, self._prog.get(), self._subsample.get()
        )
        with open(path, "wb") as fh:
            fh.write(result["bytes_io"].read())

        messagebox.showinfo(
            "Saved",
            f"Saved: {os.path.basename(path)}\n"
            f"Size: {result['size_mb']:.2f} MB\n"
            f"Savings: {result['savings_pct']:.1f}%\n"
            f"PSNR: {result['psnr']:.1f} dB",
        )

    def _pick_batch_dir(self) -> None:
        """Open a folder picker for batch processing."""
        folder = filedialog.askdirectory(
            title="Select folder with 360\u00b0 images"
        )
        if folder:
            self._batch_dir.set(folder)

    def _batch_compress(self) -> None:
        """Compress all images in the selected folder."""
        folder = self._batch_dir.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(
                "No Folder", "Select a source folder first."
            )
            return

        exts  = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
        files = [
            fp for fp in Path(folder).iterdir()
            if fp.suffix.lower() in exts
        ]
        if not files:
            messagebox.showinfo("No Images", "No image files found.")
            return

        out_dir = Path(folder) / "compressed_360"
        out_dir.mkdir(exist_ok=True)

        quality = self._quality.get()
        fmt     = self._format.get()
        ext     = "jpg" if fmt == "JPEG" else "webp"
        prog    = self._prog.get()
        sub     = self._subsample.get()

        def run() -> None:
            """Batch worker running on a background thread."""
            total       = len(files)
            saved_total = 0
            orig_total  = 0

            for idx, fp in enumerate(files):
                self.after(
                    0,
                    lambda i=idx, n=fp.name: (
                        self._lbl_batch_status.configure(  # type: ignore[union-attr]
                            text=f"[{i + 1}/{total}] {n}"
                        )
                    ),
                )
                self.after(
                    0,
                    lambda v=int(idx / total * 100): (
                        self._progress.configure(value=v)  # type: ignore[union-attr]
                    ),
                )
                eng = CompressionEngine()
                if not eng.load_image(str(fp)):
                    continue
                result    = eng.compress(quality, fmt, prog, sub)
                out_path  = out_dir / (fp.stem + f"_q{quality}.{ext}")
                with open(str(out_path), "wb") as fh:
                    fh.write(result["bytes_io"].read())
                saved_total += eng.original_bytes - result["size_bytes"]
                orig_total  += eng.original_bytes

            saved_mb = saved_total / 1_048_576
            orig_mb  = orig_total / 1_048_576
            self.after(
                0, lambda: self._progress.configure(value=100)  # type: ignore[union-attr]
            )
            pct = saved_mb / orig_mb * 100 if orig_mb > 0 else 0
            self.after(
                0,
                lambda: self._lbl_batch_status.configure(  # type: ignore[union-attr]
                    text=(
                        f"\u2705 Done! {total} files."
                        f" Saved {saved_mb:.1f} MB"
                        f" of {orig_mb:.1f} MB total."
                    )
                ),
            )
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Batch Complete",
                    f"Processed {total} images\n"
                    f"Output: {out_dir}\n"
                    f"Total saved: {saved_mb:.1f} MB ({pct:.0f}%)",
                ),
            )

        threading.Thread(target=run, daemon=True).start()

    # ── EMPTY STATE ─────────────────────────────────────────
    def _update_empty_state(self) -> None:
        """Show placeholder text in both preview canvases."""
        for canvas in (self._canvas_orig, self._canvas_comp):
            if canvas:
                canvas.delete("all")
                canvas.create_text(
                    PREVIEW_W // 2, PREVIEW_H // 2,
                    text=(
                        "No image loaded\n"
                        "Click  \U0001f4c2 OPEN IMAGE  to begin"
                    ),
                    fill=TEXT_MUTED, font=FONT_LABEL, justify="center",
                )


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main() -> None:
    """Launch the 360° optimizer application."""
    app = App360Optimizer()
    app.update_idletasks()
    screen_w = app.winfo_screenwidth()
    screen_h = app.winfo_screenheight()
    win_w, win_h = 1280, 860
    app.geometry(
        f"{win_w}x{win_h}+{(screen_w - win_w) // 2}+{(screen_h - win_h) // 2}"
    )
    app.mainloop()


if __name__ == "__main__":
    main()