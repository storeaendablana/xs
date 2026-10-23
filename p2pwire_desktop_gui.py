# ==============================================================================
# P2Pwire Ultra-Turbo Desktop Bridge & Audio Streamer (Dynamic Cyberpunk Edition)
# Requirements: pip install customtkinter soundcard numpy websockets
# ==============================================================================

import customtkinter as ctk
import asyncio
import threading
import socket
import subprocess
import platform
import concurrent.futures
import urllib.request
import tkinter.filedialog as filedialog
import tkinter as tk
import os
import struct
import time
import math
import random

# Optional high-fidelity audio capture modules
try:
    import soundcard as sc
    HAS_SOUNDCARD = True
except ImportError:
    HAS_SOUNDCARD = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --- HBSP Turbo Protocol Constants ---
HBSP_MAGIC = b'HBSP'
HBSP_VERSION = 2

OP_HANDSHAKE_INIT = 1
OP_HANDSHAKE_ACK = 2
OP_CHUNK_DATA = 3
OP_CHUNK_ACK = 4
OP_FILE_COMPLETE = 5
OP_COMPLETE_ACK = 6
OP_CANCEL = 7

OP_TURBO_INIT = 0x10
OP_TURBO_INIT_ACK = 0x11
OP_TURBO_CHANNEL_JOIN = 0x12
OP_TURBO_CHANNEL_ACK = 0x13
OP_TURBO_COMPLETE = 0x14
OP_TURBO_COMPLETE_ACK = 0x15

# Optimized pipeline chunk size: 128 KB keeps TCP window filled at wire rate without GIL contention
CHUNK_SIZE = 128 * 1024
DEFAULT_CHANNELS = 4

# Cyberpunk Neon Palette
CLR_BG = "#090D16"
CLR_CARD = "#111827"
CLR_CARD_BORDER = "#1E293B"
CLR_ACCENT_CYAN = "#06B6D4"
CLR_ACCENT_GREEN = "#10B981"
CLR_ACCENT_PURPLE = "#8B5CF6"
CLR_ACCENT_PINK = "#EC4899"
CLR_ACCENT_AMBER = "#F59E0B"
CLR_TEXT_MUTED = "#94A3B8"
CLR_TEXT_BRIGHT = "#F8FAFC"


class P2PwireApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("P2Pwire Turbo Studio • Real-Time Audio & Wire-Speed Files")
        self.geometry("640x840")
        self.minsize(580, 780)
        self.configure(fg_color=CLR_BG)

        # State Variables
        self.is_streaming = False
        self.stream_lock = threading.Lock()
        self.stop_stream_event = threading.Event()
        self.stream_thread = None
        self.stream_loop = None

        self.is_scanning = False
        self.hbsp_cancel_flag = False
        self.is_transferring = False

        self.selected_file_path = ctk.StringVar(value="")
        self.transfer_progress = ctk.StringVar(value="Status: Ready for Wire-Speed Transfer")
        self.active_turbo_sessions = {}

        # Default save directory
        default_save_dir = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireTransfers")
        os.makedirs(default_save_dir, exist_ok=True)
        self.save_directory_path = ctk.StringVar(value=default_save_dir)

        # Animation state
        self.anim_tick = 0
        self.waveform_levels = [0.0] * 44
        self.waveform_peaks = [0.0] * 44
        self.speed_history = [0.0] * 30
        self.laser_pos = 0.0

        # Build UI
        self.setup_header()
        self.setup_tabs()
        self.setup_connection_bar()
        self.setup_console()

        # Start background listeners & animation ticker
        self.animate_loop()
        threading.Thread(target=self.detect_android_device, args=(True,), daemon=True).start()
        threading.Thread(target=self.run_pc_hbsp_receiver, daemon=True).start()

    # =========================================================================
    # UI SETUP
    # =========================================================================
    def setup_header(self):
        header_card = ctk.CTkFrame(self, fg_color=CLR_CARD, corner_radius=14, border_width=1, border_color=CLR_CARD_BORDER)
        header_card.pack(fill="x", padx=16, pady=(12, 6))

        top_row = ctk.CTkFrame(header_card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(10, 2))

        # Title + Badge
        title_box = ctk.CTkFrame(top_row, fg_color="transparent")
        title_box.pack(side="left")

        lbl_title = ctk.CTkLabel(
            title_box,
            text="⚡ P2Pwire Turbo Studio",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CLR_TEXT_BRIGHT
        )
        lbl_title.pack(anchor="w")

        lbl_sub = ctk.CTkLabel(
            title_box,
            text="Ultra-Low Latency Audio Stream • 4x Turbo Socket Engine (50–100+ MB/s)",
            font=ctk.CTkFont(size=11),
            text_color=CLR_TEXT_MUTED
        )
        lbl_sub.pack(anchor="w")

        # Dynamic Status Pill
        self.status_pill = ctk.CTkLabel(
            top_row,
            text="● STANDBY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_ACCENT_AMBER,
            fg_color="#1E293B",
            corner_radius=12,
            padx=12,
            pady=4
        )
        self.status_pill.pack(side="right")

    def setup_tabs(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=CLR_CARD,
            segmented_button_fg_color="#0F172A",
            segmented_button_selected_color=CLR_ACCENT_CYAN,
            segmented_button_selected_hover_color="#0891B2",
            segmented_button_unselected_color="#1E293B",
            segmented_button_unselected_hover_color="#334155",
            corner_radius=14,
            border_width=1,
            border_color=CLR_CARD_BORDER
        )
        self.tabview.pack(fill="both", expand=True, padx=16, pady=4)

        self.tab_audio = self.tabview.add("🔊 Live System Audio")
        self.tab_files = self.tabview.add("⚡ Turbo Gigabit Files")

        self.setup_audio_tab_content()
        self.setup_files_tab_content()

    def setup_audio_tab_content(self):
        tab = self.tab_audio

        # Top Audio Info Banner
        info_banner = ctk.CTkFrame(tab, fg_color="#0B132B", corner_radius=10, border_width=1, border_color="#1E293B")
        info_banner.pack(fill="x", padx=12, pady=(8, 6))

        ctk.CTkLabel(
            info_banner,
            text="🎵 Pure 48,000 Hz 16-Bit PCM • Hardware WASAPI Loopback • Zero Compression",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_ACCENT_CYAN
        ).pack(pady=6)

        # Dynamic Visualizer Canvas
        self.canvas_visualizer = tk.Canvas(
            tab,
            width=580,
            height=140,
            bg="#0B0F19",
            highlightthickness=1,
            highlightbackground="#1E293B"
        )
        self.canvas_visualizer.pack(fill="x", padx=12, pady=6)

        # Pre-create bars and peak lines on canvas
        self.bar_items = []
        self.peak_items = []
        bar_count = 44
        bar_w = 9
        gap = 4
        start_x = 10
        for i in range(bar_count):
            x1 = start_x + i * (bar_w + gap)
            x2 = x1 + bar_w
            # Bar rectangle
            b = self.canvas_visualizer.create_rectangle(x1, 70, x2, 70, fill="#1E293B", outline="")
            # Peak cap
            p = self.canvas_visualizer.create_line(x1, 70, x2, 70, fill="#38BDF8", width=2)
            self.bar_items.append(b)
            self.peak_items.append(p)

        self.canvas_hint = self.canvas_visualizer.create_text(
            290, 70,
            text="✦ Audio Engine Idle • Ready to Stream PC System Sound",
            fill="#64748B",
            font=("Segoe UI", 11, "italic")
        )

        # Stream Toggle Button (Glowing modern button)
        self.btn_toggle_stream = ctk.CTkButton(
            tab,
            text="▶ Start Streaming PC System Audio to Phone",
            command=self.toggle_stream,
            height=46,
            corner_radius=10,
            fg_color=CLR_ACCENT_CYAN,
            hover_color="#0891B2",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_toggle_stream.pack(fill="x", padx=12, pady=(8, 6))

        # Audio Metrics HUD
        hud_frame = ctk.CTkFrame(tab, fg_color="#0F172A", corner_radius=10, border_width=1, border_color="#1E293B")
        hud_frame.pack(fill="x", padx=12, pady=4)

        metrics = [
            ("Sample Rate", "48.0 kHz"),
            ("Format", "16-bit Mono"),
            ("Wire Latency", "~12 ms"),
            ("Audio Channel", "Direct PCM")
        ]
        for idx, (label, val) in enumerate(metrics):
            sub = ctk.CTkFrame(hud_frame, fg_color="transparent")
            sub.pack(side="left", expand=True, fill="both", padx=6, pady=6)
            ctk.CTkLabel(sub, text=label, font=ctk.CTkFont(size=10), text_color=CLR_TEXT_MUTED).pack()
            ctk.CTkLabel(sub, text=val, font=ctk.CTkFont(size=11, weight="bold"), text_color=CLR_TEXT_BRIGHT).pack()

    def setup_files_tab_content(self):
        tab = self.tab_files

        # Protocol badge
        proto_card = ctk.CTkFrame(tab, fg_color="#06281E", corner_radius=10, border_width=1, border_color="#0F5132")
        proto_card.pack(fill="x", padx=12, pady=(8, 6))

        ctk.CTkLabel(
            proto_card,
            text="⚡ Turbo HBSP 2.0 Engine • 4x Parallel Sockets • Direct Flash Pipeline (Up to 100+ MB/s)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_ACCENT_GREEN
        ).pack(pady=6)

        # File Drop/Select Card
        select_card = ctk.CTkFrame(tab, fg_color="#0F172A", corner_radius=10, border_width=1, border_color="#1E293B")
        select_card.pack(fill="x", padx=12, pady=4)

        btn_select = ctk.CTkButton(
            select_card,
            text="📁 Select File from PC (Videos, Movies, Large Folders)",
            command=self.select_file,
            height=38,
            corner_radius=8,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        btn_select.pack(fill="x", padx=12, pady=(10, 4))

        self.lbl_selected_file = ctk.CTkLabel(
            select_card,
            textvariable=self.selected_file_path,
            font=ctk.CTkFont(size=11),
            text_color=CLR_ACCENT_CYAN,
            wraplength=520
        )
        self.lbl_selected_file.pack(padx=12, pady=(0, 8))

        # Dynamic Shimmer Transfer Progress Canvas
        self.canvas_progress = tk.Canvas(
            tab,
            width=580,
            height=42,
            bg="#0B0F19",
            highlightthickness=1,
            highlightbackground="#1E293B"
        )
        self.canvas_progress.pack(fill="x", padx=12, pady=6)
        self.progress_rect = self.canvas_progress.create_rectangle(0, 0, 0, 42, fill="#047857", outline="")
        self.laser_rect = self.canvas_progress.create_rectangle(0, 0, 0, 42, fill="#34D399", outline="", state="hidden")
        self.progress_text = self.canvas_progress.create_text(
            290, 21,
            text="0% • Ready for Wire-Speed Transfer",
            fill="#E2E8F0",
            font=("Segoe UI", 11, "bold")
        )

        # Real-time Speed & Stats HUD
        stats_frame = ctk.CTkFrame(tab, fg_color="#0F172A", corner_radius=10, border_width=1, border_color="#1E293B")
        stats_frame.pack(fill="x", padx=12, pady=4)

        self.lbl_speed_val = ctk.CTkLabel(
            stats_frame,
            text="0.0 MB/s",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=CLR_ACCENT_GREEN
        )
        self.lbl_speed_val.pack(pady=(4, 0))

        self.lbl_transfer_stats = ctk.CTkLabel(
            stats_frame,
            textvariable=self.transfer_progress,
            font=ctk.CTkFont(size=11),
            text_color=CLR_TEXT_MUTED
        )
        self.lbl_transfer_stats.pack(pady=(0, 6))

        # Save Directory Card
        save_card = ctk.CTkFrame(tab, fg_color="#0F172A", corner_radius=10, border_width=1, border_color="#1E293B")
        save_card.pack(fill="x", padx=12, pady=4)

        save_row = ctk.CTkFrame(save_card, fg_color="transparent")
        save_row.pack(fill="x", padx=10, pady=(6, 2))

        ctk.CTkLabel(
            save_row,
            text="📥 PC Save Location (Incoming from Phone):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_TEXT_MUTED
        ).pack(side="left")

        ctk.CTkButton(
            save_row,
            text="📂 Change Folder",
            command=self.choose_save_directory,
            width=110,
            height=26,
            corner_radius=6,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=11)
        ).pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            save_row,
            text="🗁 Open",
            command=self.open_save_directory,
            width=65,
            height=26,
            corner_radius=6,
            fg_color="#0F766E",
            hover_color="#0D9488",
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(side="right")

        self.lbl_save_dir = ctk.CTkLabel(
            save_card,
            textvariable=self.save_directory_path,
            font=ctk.CTkFont(size=10),
            text_color=CLR_ACCENT_CYAN,
            wraplength=520,
            anchor="w"
        )
        self.lbl_save_dir.pack(fill="x", padx=10, pady=(0, 6))

        # Action Buttons
        action_row = ctk.CTkFrame(tab, fg_color="transparent")
        action_row.pack(fill="x", padx=12, pady=(6, 8))

        self.btn_send_file = ctk.CTkButton(
            action_row,
            text="🚀 Start Wire-Speed Turbo Transfer",
            command=self.send_file_hbsp,
            height=42,
            corner_radius=10,
            fg_color=CLR_ACCENT_GREEN,
            hover_color="#059669",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.btn_send_file.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_cancel_file = ctk.CTkButton(
            action_row,
            text="✕ Cancel",
            command=self.cancel_hbsp_transfer,
            height=42,
            width=85,
            corner_radius=10,
            fg_color="#EF4444",
            hover_color="#DC2626",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_cancel_file.pack(side="right")

    def setup_connection_bar(self):
        conn_frame = ctk.CTkFrame(self, fg_color=CLR_CARD, corner_radius=14, border_width=1, border_color=CLR_CARD_BORDER)
        conn_frame.pack(fill="x", padx=16, pady=4)

        lbl = ctk.CTkLabel(
            conn_frame,
            text="📱 Android Phone IP Address:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CLR_TEXT_BRIGHT
        )
        lbl.pack(anchor="w", padx=14, pady=(8, 2))

        input_row = ctk.CTkFrame(conn_frame, fg_color="transparent")
        input_row.pack(fill="x", padx=14, pady=(0, 8))

        self.ip_entry = ctk.CTkEntry(
            input_row,
            placeholder_text="e.g. 192.168.43.1 or 192.168.1.x",
            height=38,
            corner_radius=8,
            font=ctk.CTkFont(size=13)
        )
        self.ip_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.ip_entry.insert(0, "192.168.43.1")

        self.btn_detect = ctk.CTkButton(
            input_row,
            text="🔍 Auto-Detect",
            command=self.start_auto_detect_thread,
            width=120,
            height=38,
            corner_radius=8,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_detect.pack(side="right")

    def setup_console(self):
        console_frame = ctk.CTkFrame(self, fg_color=CLR_CARD, corner_radius=14, border_width=1, border_color=CLR_CARD_BORDER)
        console_frame.pack(fill="x", padx=16, pady=(4, 12))

        header = ctk.CTkFrame(console_frame, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(6, 2))

        ctk.CTkLabel(
            header,
            text="📟 Real-Time Activity Log",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_TEXT_MUTED
        ).pack(side="left")

        ctk.CTkButton(
            header,
            text="Clear",
            command=lambda: self.log_box.delete("1.0", "end"),
            width=50,
            height=20,
            corner_radius=4,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=10)
        ).pack(side="right")

        self.log_box = ctk.CTkTextbox(
            console_frame,
            height=85,
            corner_radius=8,
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color="#38BDF8",
            fg_color="#080C14"
        )
        self.log_box.pack(fill="x", padx=12, pady=(0, 8))
        self.log("P2Pwire Turbo Studio Engine initialized. Wire Speed Ready.")

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}\n"
        print(entry, end="")
        try:
            self.log_box.insert("end", entry)
            self.log_box.see("end")
        except Exception:
            pass

    # =========================================================================
    # DYNAMIC ANIMATION ENGINE
    # =========================================================================
    def animate_loop(self):
        self.anim_tick += 1

        # 1. Animate Audio Visualizer
        if not self.is_streaming:
            # Gentle breathing wave animation when idle
            self.canvas_visualizer.itemconfig(self.canvas_hint, state="normal")
            for i in range(len(self.bar_items)):
                wave = math.sin(self.anim_tick * 0.08 + i * 0.22) * 12 + 14
                self.canvas_visualizer.coords(self.bar_items[i], 10 + i * 13, 70 - wave, 10 + i * 13 + 9, 70 + wave)
                self.canvas_visualizer.itemconfig(self.bar_items[i], fill="#1E293B")
                self.canvas_visualizer.coords(self.peak_items[i], 10 + i * 13, 70 - wave - 2, 10 + i * 13 + 9, 70 - wave - 2)
                self.canvas_visualizer.itemconfig(self.peak_items[i], fill="#334155")
        else:
            self.canvas_visualizer.itemconfig(self.canvas_hint, state="hidden")
            # Dynamic decay physics for audio bars
            for i in range(len(self.bar_items)):
                cur = self.waveform_levels[i]
                peak = self.waveform_peaks[i]

                # Decay peak cap slowly
                peak = max(cur, peak - 1.2)
                self.waveform_peaks[i] = peak

                # Decay bar height smoothly
                self.waveform_levels[i] = max(2.0, cur * 0.88)

                # Gradient color transition: Cyan -> Violet -> Pink
                if cur > 50:
                    bar_color = CLR_ACCENT_PINK
                elif cur > 28:
                    bar_color = CLR_ACCENT_PURPLE
                else:
                    bar_color = CLR_ACCENT_CYAN

                x1 = 10 + i * 13
                x2 = x1 + 9
                self.canvas_visualizer.coords(self.bar_items[i], x1, 70 - cur, x2, 70 + cur)
                self.canvas_visualizer.itemconfig(self.bar_items[i], fill=bar_color)

                self.canvas_visualizer.coords(self.peak_items[i], x1, 70 - peak - 2, x2, 70 - peak - 2)
                self.canvas_visualizer.itemconfig(self.peak_items[i], fill="#38BDF8")

        # 2. Animate File Transfer Laser Shimmer
        if self.is_transferring:
            self.laser_pos = (self.laser_pos + 18) % 580
            self.canvas_progress.itemconfig(self.laser_rect, state="normal")
            self.canvas_progress.coords(self.laser_rect, self.laser_pos, 0, self.laser_pos + 35, 42)
        else:
            self.canvas_progress.itemconfig(self.laser_rect, state="hidden")

        # Re-schedule next frame (approx 33 FPS)
        self.after(30, self.animate_loop)

    def update_waveform_from_pcm(self, pcm_bytes):
        if not HAS_NUMPY or len(pcm_bytes) < 80:
            return
        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            chunk_len = len(samples) // 44
            if chunk_len == 0:
                return
            for i in range(44):
                chunk = samples[i * chunk_len : (i + 1) * chunk_len]
                amp = (np.abs(chunk).mean() / 32768.0) * 62.0
                if amp > self.waveform_levels[i]:
                    self.waveform_levels[i] = min(64.0, amp)
        except Exception:
            pass

    # =========================================================================
    # AUDIO STREAMING LOGIC (Bulletproof Single-Thread Architecture)
    # =========================================================================
    def toggle_stream(self):
        if not self.is_streaming:
            ip = self.ip_entry.get().strip()
            if not ip:
                self.log("Set phone IP first or click Auto-Detect.")
                return

            with self.stream_lock:
                self.is_streaming = True
                self.stop_stream_event.clear()
                self.btn_toggle_stream.configure(
                    text="⏹ Stop Streaming System Audio",
                    fg_color="#EF4444",
                    hover_color="#DC2626"
                )
                self.status_pill.configure(text="● CONNECTING...", text_color=CLR_ACCENT_CYAN)
                self.log(f"Initiating audio connection to Android phone at ws://{ip}:8080/ws/upload_audio...")

                self.stream_thread = threading.Thread(target=self.run_audio_worker, args=(ip,), daemon=True)
                self.stream_thread.start()
        else:
            self.stop_streaming_cleanly()

    def stop_streaming_cleanly(self):
        with self.stream_lock:
            self.is_streaming = False
            self.stop_stream_event.set()
            self.btn_toggle_stream.configure(
                text="▶ Start Streaming PC System Audio to Phone",
                fg_color=CLR_ACCENT_CYAN,
                hover_color="#0891B2"
            )
            self.status_pill.configure(text="● STANDBY", text_color=CLR_ACCENT_AMBER)
            self.log("Audio streaming stopped by user.")

    def run_audio_worker(self, ip):
        async def stream():
            uri = f"ws://{ip}:8080/ws/upload_audio"
            reconnect_attempts = 0

            while self.is_streaming and not self.stop_stream_event.is_set():
                ws = None
                try:
                    self.log(f"Connecting WebSocket to {uri}...")
                    ws = await websockets.connect(
                        uri,
                        ping_interval=20,
                        ping_timeout=15,
                        close_timeout=4,
                        max_size=None
                    )
                    reconnect_attempts = 0
                    self.after(0, lambda: self.status_pill.configure(text="● STREAMING LIVE", text_color=CLR_ACCENT_GREEN))
                    self.log("Connected to phone speaker! Initializing loopback audio capture...")

                    # Initialize loopback microphone
                    if not HAS_SOUNDCARD:
                        raise RuntimeError("Python 'soundcard' module not found. Run: pip install soundcard")

                    speaker = sc.default_speaker()
                    mic = None
                    try:
                        mic = sc.get_microphone(id=speaker.name, include_loopback=True)
                    except Exception:
                        # Fallback: scan all loopback mics
                        for m in sc.all_microphones(include_loopback=True):
                            if m.isloopback:
                                mic = m
                                break

                    if not mic:
                        raise RuntimeError("No system audio loopback device available. Ensure speakers are active.")

                    self.log(f"Loopback device active: {mic.name}")
                    audio_queue = asyncio.Queue(maxsize=20)
                    capture_stop = threading.Event()

                    # Audio capture thread
                    def capture_task():
                        # Try 48000 Hz, fallback to 44100 Hz if hardware requires
                        sample_rates = [48000, 44100]
                        active_rate = 48000
                        for rate in sample_rates:
                            try:
                                with mic.recorder(samplerate=rate, channels=2) as recorder:
                                    active_rate = rate
                                    while self.is_streaming and not capture_stop.is_set() and not self.stop_stream_event.is_set():
                                        data = recorder.record(numframes=960)
                                        # Downmix to mono
                                        mono = np.mean(data, axis=1)
                                        mono = np.clip(mono, -1.0, 1.0)
                                        pcm16 = (mono * 32767).astype(np.int16).tobytes()

                                        if audio_queue.full():
                                            try:
                                                audio_queue.get_nowait()
                                            except Exception:
                                                pass

                                        self.stream_loop.call_soon_threadsafe(audio_queue.put_nowait, pcm16)
                                break
                            except Exception as ex:
                                if rate == sample_rates[-1]:
                                    self.log(f"[CAPTURE FAULT] {ex}")

                    c_thread = threading.Thread(target=capture_task, daemon=True)
                    c_thread.start()

                    seq = 0
                    while self.is_streaming and not self.stop_stream_event.is_set():
                        try:
                            pcm16 = await asyncio.wait_for(audio_queue.get(), timeout=1.2)
                        except asyncio.TimeoutError:
                            # Send keep-alive packet if quiet
                            pcm16 = bytes(960 * 2)

                        # P2Pwire Audio Packet Header: [0xAB, 0x46, 0x01, SEQ_HI, SEQ_LO, 0, 0, 0]
                        h = bytearray(8)
                        h[0], h[1], h[2] = 0xAB, 0x46, 0x01
                        h[3], h[4] = (seq >> 8) & 0xFF, seq & 0xFF
                        seq = (seq + 1) & 0xFFFF

                        await ws.send(bytes(h) + pcm16)
                        self.after(0, self.update_waveform_from_pcm, pcm16)

                    capture_stop.set()

                except Exception as ex:
                    if self.is_streaming and not self.stop_stream_event.is_set():
                        self.log(f"[CONNECTION NOTICE] {ex}")

                finally:
                    if ws:
                        try:
                            await ws.close()
                        except Exception:
                            pass

                if not self.is_streaming or self.stop_stream_event.is_set():
                    break

                # Controlled backoff prevents reconnect storms
                reconnect_attempts += 1
                if reconnect_attempts > 6:
                    self.log("Maximum connection attempts reached. Check Phone IP and Wi-Fi.")
                    break

                backoff = 2
                self.after(0, lambda a=reconnect_attempts: self.status_pill.configure(
                    text=f"● RECONNECTING (#{a})...", text_color=CLR_ACCENT_AMBER
                ))
                await asyncio.sleep(backoff)

            self.after(0, self.stop_streaming_cleanly)

        self.stream_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.stream_loop)
        try:
            self.stream_loop.run_until_complete(stream())
        except Exception as e:
            self.log(f"[FATAL STREAM ERROR] {e}")

    # =========================================================================
    # TURBO FILE TRANSFER ENGINE (4x Parallel Sockets, Wire-Speed Sockets)
    # =========================================================================
    def select_file(self):
        file = filedialog.askopenfilename()
        if file:
            size_mb = os.path.getsize(file) / (1024 * 1024)
            name = os.path.basename(file)
            self.selected_file_path.set(f"📄 {name} ({size_mb:.1f} MB)")
            self._current_file_path = file

    def choose_save_directory(self):
        cur = self.save_directory_path.get()
        d = filedialog.askdirectory(initialdir=cur if os.path.exists(cur) else os.path.expanduser("~"))
        if d:
            self.save_directory_path.set(d)
            self.log(f"PC Save location changed to: {d}")

    def open_save_directory(self):
        d = self.save_directory_path.get()
        if os.path.exists(d):
            try:
                if platform.system() == "Windows":
                    os.startfile(d)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", d])
                else:
                    subprocess.Popen(["xdg-open", d])
            except Exception as e:
                self.log(f"Cannot open folder: {e}")

    def cancel_hbsp_transfer(self):
        self.hbsp_cancel_flag = True
        self.log("Cancelling wire transfer...")

    def update_transfer_ui(self, progress, status_text, speed_mbps=0.0):
        # Update Canvas Shimmer Bar
        bar_width = int(progress * 580)
        self.canvas_progress.coords(self.progress_rect, 0, 0, bar_width, 42)
        pct_label = f"⚡ {int(progress * 100)}% • {status_text}"
        self.canvas_progress.itemconfig(self.progress_text, text=pct_label)

        self.transfer_progress.set(status_text)
        self.lbl_speed_val.configure(text=f"{speed_mbps:.1f} MB/s")

    def recv_exact(self, sock, num_bytes):
        data = bytearray()
        while len(data) < num_bytes:
            packet = sock.recv(num_bytes - len(data))
            if not packet:
                raise EOFError(f"Connection closed while reading {num_bytes} bytes")
            data.extend(packet)
        return bytes(data)

    def send_file_hbsp(self):
        ip = self.ip_entry.get().strip()
        file_path = getattr(self, '_current_file_path', None)
        if not ip or not file_path or not os.path.exists(file_path):
            self.log("Error: Set valid Phone IP and Select a File first")
            return

        self.btn_send_file.configure(state="disabled")
        threading.Thread(target=self.perform_hbsp_turbo_upload, args=(ip, file_path), daemon=True).start()

    def perform_hbsp_turbo_upload(self, ip, file_path):
        self.hbsp_cancel_flag = False
        self.is_transferring = True
        master_sock = None
        try:
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)
            name_bytes = file_name.encode('utf-8')

            num_channels = DEFAULT_CHANNELS
            session_id = random.randint(100000, 999999)

            self.log(f"Opening {num_channels}x Turbo Gigabit Sockets to {ip}:8082 for {file_name} ({file_size/(1024*1024):.1f} MB)...")
            self.after(0, lambda: self.update_transfer_ui(0.01, f"Connecting to {ip}:8082..."))

            # 1. Connect Master Control Socket
            master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            master_sock.settimeout(10.0)
            master_sock.connect((ip, 8082))

            # Send OP_TURBO_INIT
            init_header = bytearray()
            init_header.extend(HBSP_MAGIC)
            init_header.append(HBSP_VERSION)
            init_header.append(OP_TURBO_INIT)
            init_header.extend(struct.pack('>I', session_id))
            init_header.extend(struct.pack('>Q', file_size))
            init_header.append(num_channels)
            init_header.extend(struct.pack('>H', len(name_bytes)))
            init_header.extend(name_bytes)
            master_sock.sendall(init_header)

            # Receive INIT_ACK (11 bytes)
            ack_data = self.recv_exact(master_sock, 11)
            if not ack_data.startswith(HBSP_MAGIC) or ack_data[5] != OP_TURBO_INIT_ACK or ack_data[10] != 0:
                raise Exception(f"Turbo Handshake rejected by Phone: {ack_data}")

            self.log("Turbo Handshake accepted! Sockets engaged. Wire streaming active...")

            # 2. Parallel Worker Channels
            part_size = file_size // num_channels
            thread_sent = [0] * num_channels
            worker_errors = []

            def worker_channel(ch_idx, start_offset, length):
                wsock = None
                try:
                    wsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    wsock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    try:
                        wsock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
                    except Exception:
                        pass
                    wsock.settimeout(15.0)
                    wsock.connect((ip, 8082))

                    # Send OP_TURBO_CHANNEL_JOIN
                    jbuf = bytearray()
                    jbuf.extend(HBSP_MAGIC)
                    jbuf.append(HBSP_VERSION)
                    jbuf.append(OP_TURBO_CHANNEL_JOIN)
                    jbuf.extend(struct.pack('>I', session_id))
                    jbuf.append(ch_idx)
                    jbuf.extend(struct.pack('>Q', start_offset))
                    jbuf.extend(struct.pack('>Q', length))
                    wsock.sendall(jbuf)

                    # Receive JOIN_ACK (7 bytes)
                    jack = self.recv_exact(wsock, 7)
                    if jack[5] != OP_TURBO_CHANNEL_ACK or jack[6] != 0:
                        raise Exception(f"Channel {ch_idx} join failed")

                    # High-throughput 128KB memoryview wire stream
                    buf = bytearray(CHUNK_SIZE)
                    mv = memoryview(buf)
                    sent = 0
                    with open(file_path, 'rb') as f:
                        f.seek(start_offset)
                        while sent < length and not self.hbsp_cancel_flag:
                            to_read = min(CHUNK_SIZE, length - sent)
                            n = f.readinto(mv[:to_read])
                            if not n:
                                break
                            wsock.sendall(mv[:n])
                            sent += n
                            thread_sent[ch_idx] = sent
                except Exception as ex:
                    worker_errors.append(f"Channel {ch_idx}: {ex}")
                finally:
                    if wsock:
                        try:
                            wsock.close()
                        except Exception:
                            pass

            start_time = time.time()
            last_calc_time = start_time
            last_bytes_sample = 0

            threads = []
            for ch in range(num_channels):
                start_off = ch * part_size
                ch_len = (file_size - start_off) if ch == (num_channels - 1) else part_size
                t = threading.Thread(target=worker_channel, args=(ch, start_off, ch_len), daemon=True)
                threads.append(t)
                t.start()

            # Dynamic speed & progress monitoring
            while any(t.is_alive() for t in threads) and not self.hbsp_cancel_flag:
                time.sleep(0.15)
                now = time.time()
                diff = now - last_calc_time
                if diff >= 0.2:
                    cur_up = sum(thread_sent)
                    bytes_diff = cur_up - last_bytes_sample
                    current_speed = (bytes_diff / (1024 * 1024)) / diff
                    pct = cur_up / file_size if file_size > 0 else 0
                    rem_bytes = max(0, file_size - cur_up)
                    eta = int((rem_bytes / (1024 * 1024)) / current_speed) if current_speed > 0.1 else 0

                    up_mb = cur_up / (1024 * 1024)
                    tot_mb = file_size / (1024 * 1024)
                    status_text = f"{up_mb:.1f} / {tot_mb:.1f} MB • {num_channels}x Turbo • ETA: {eta}s"

                    self.after(0, lambda p=pct, s=status_text, sp=current_speed: self.update_transfer_ui(p, s, sp))
                    last_calc_time = now
                    last_bytes_sample = cur_up

            for t in threads:
                t.join(timeout=2.0)

            if self.hbsp_cancel_flag:
                self.log("Transfer cancelled by user.")
                self.after(0, lambda: self.update_transfer_ui(0.0, "Cancelled by user", 0.0))
                return

            if worker_errors:
                raise Exception("; ".join(worker_errors))

            # 3. Complete Handshake
            master_sock.sendall(bytes([OP_TURBO_COMPLETE]))
            cack = self.recv_exact(master_sock, 7)
            if cack[5] == OP_TURBO_COMPLETE_ACK and cack[6] == 0:
                total_time = max(0.1, time.time() - start_time)
                avg_speed = (file_size / (1024 * 1024)) / total_time
                self.log(f"✓ Wire Transfer Complete! {file_size / (1024*1024):.1f} MB in {total_time:.1f}s (Average Speed: {avg_speed:.1f} MB/s)!")
                self.after(0, lambda: self.update_transfer_ui(1.0, f"✓ Complete in {total_time:.1f}s", avg_speed))
            else:
                raise Exception("Turbo completion ACK failed")

        except Exception as e:
            self.log(f"Transfer Error: {e}")
            self.after(0, lambda: self.update_transfer_ui(0.0, f"Error: {e}", 0.0))
        finally:
            self.is_transferring = False
            if master_sock:
                try:
                    master_sock.close()
                except Exception:
                    pass
            self.after(0, lambda: self.btn_send_file.configure(state="normal"))

    # =========================================================================
    # PC INCOMING HBSP RECEIVER (Receive files sent from Android)
    # =========================================================================
    def run_pc_hbsp_receiver(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", 8082))
            srv.listen(10)
            while True:
                conn, addr = srv.accept()
                threading.Thread(target=self.handle_incoming_connection_on_pc, args=(conn, addr), daemon=True).start()
        except Exception:
            pass

    def handle_incoming_connection_on_pc(self, sock, addr):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            magic = self.recv_exact(sock, 4)
            if magic != HBSP_MAGIC:
                sock.close()
                return
            ver = sock.recv(1)[0]
            op = sock.recv(1)[0]

            if op == OP_TURBO_INIT:
                session_id = struct.unpack('>I', self.recv_exact(sock, 4))[0]
                file_size = struct.unpack('>Q', self.recv_exact(sock, 8))[0]
                channels_cnt = sock.recv(1)[0]
                name_len = struct.unpack('>H', self.recv_exact(sock, 2))[0]
                file_name = self.recv_exact(sock, name_len).decode('utf-8')

                downloads_dir = self.save_directory_path.get()
                os.makedirs(downloads_dir, exist_ok=True)
                out_path = os.path.join(downloads_dir, file_name)

                # Pre-allocate contiguous disk file
                with open(out_path, 'wb') as f:
                    f.truncate(file_size)
                self.active_turbo_sessions[session_id] = out_path

                # Reply INIT_ACK
                ack = bytearray()
                ack.extend(HBSP_MAGIC)
                ack.append(HBSP_VERSION)
                ack.append(OP_TURBO_INIT_ACK)
                ack.extend(struct.pack('>I', session_id))
                ack.append(0)
                sock.sendall(ack)

                self.log(f"Incoming file from phone: {file_name} ({file_size/(1024*1024):.1f} MB)...")

                # Wait for OP_TURBO_COMPLETE
                comp_op = sock.recv(1)
                self.active_turbo_sessions.pop(session_id, None)

                cack = bytearray()
                cack.extend(HBSP_MAGIC)
                cack.append(HBSP_VERSION)
                cack.append(OP_TURBO_COMPLETE_ACK)
                cack.append(0)
                sock.sendall(cack)
                self.log(f"✓ Saved incoming file to: {out_path}")

            elif op == OP_TURBO_CHANNEL_JOIN:
                session_id = struct.unpack('>I', self.recv_exact(sock, 4))[0]
                ch_idx = sock.recv(1)[0]
                start_offset = struct.unpack('>Q', self.recv_exact(sock, 8))[0]
                length = struct.unpack('>Q', self.recv_exact(sock, 8))[0]

                jack = bytearray()
                jack.extend(HBSP_MAGIC)
                jack.append(HBSP_VERSION)
                jack.append(OP_TURBO_CHANNEL_ACK)
                jack.append(0)
                sock.sendall(jack)

                target_path = self.active_turbo_sessions.get(session_id)
                start_t = time.time()
                while not target_path and (time.time() - start_t < 3.0):
                    time.sleep(0.04)
                    target_path = self.active_turbo_sessions.get(session_id)

                if target_path:
                    buf = bytearray(CHUNK_SIZE)
                    mv = memoryview(buf)
                    with open(target_path, 'r+b') as f:
                        f.seek(start_offset)
                        received = 0
                        while received < length:
                            to_read = min(CHUNK_SIZE, length - received)
                            filled = 0
                            while filled < to_read:
                                n = sock.recv_into(mv[filled:to_read])
                                if not n:
                                    break
                                filled += n
                            if filled == 0:
                                break
                            f.write(mv[:filled])
                            received += filled
        except Exception as e:
            self.log(f"PC Receiver Notice: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass

    # =========================================================================
    # AUTO-DETECTION ENGINE
    # =========================================================================
    def probe_ip(self, ip):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.18)
            res = sock.connect_ex((ip, 8080))
            sock.close()
            if res == 0:
                req = urllib.request.Request(f"http://{ip}:8080/api/session/ACTIVE", headers={"User-Agent": "P2PwireDiscovery"})
                with urllib.request.urlopen(req, timeout=0.35) as response:
                    if response.status == 200:
                        return ip
        except Exception:
            pass
        return None

    def start_auto_detect_thread(self):
        if self.is_scanning:
            return
        threading.Thread(target=self.detect_android_device, args=(False,), daemon=True).start()

    def detect_android_device(self, silent_startup=False):
        self.is_scanning = True
        if not silent_startup:
            self.after(0, lambda: self.btn_detect.configure(text="⏳ Scanning...", state="disabled"))
            self.after(0, lambda: self.status_pill.configure(text="● SCANNING SUBNET...", text_color=CLR_ACCENT_CYAN))
            self.log("Scanning local Wi-Fi / Hotspot for Android phone...")

        candidates = []
        try:
            if platform.system() == "Windows":
                out = subprocess.check_output("route print 0.0.0.0", shell=True, text=True, timeout=2)
                for line in out.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                        gw = parts[2]
                        if gw not in ("0.0.0.0", "127.0.0.1") and gw not in candidates:
                            candidates.append(gw)
        except Exception:
            pass

        for gw in ["192.168.43.1", "192.168.44.1", "192.168.50.1", "192.168.42.129"]:
            if gw not in candidates:
                candidates.append(gw)

        found_ip = None
        for cand in candidates:
            if self.probe_ip(cand):
                found_ip = cand
                break

        if not found_ip:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                my_local_ip = s.getsockname()[0]
                s.close()
                prefix = ".".join(my_local_ip.split(".")[:3])

                with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                    futures = [executor.submit(self.probe_ip, f"{prefix}.{i}") for i in range(1, 255)]
                    for future in concurrent.futures.as_completed(futures):
                        res = future.result()
                        if res:
                            found_ip = res
                            break
            except Exception:
                pass

        self.is_scanning = False
        if found_ip:
            self.after(0, lambda: self.ip_entry.delete(0, "end"))
            self.after(0, lambda: self.ip_entry.insert(0, found_ip))
            self.after(0, lambda: self.status_pill.configure(text=f"● PHONE DETECTED: {found_ip}", text_color=CLR_ACCENT_GREEN))
            self.log(f"Android Phone located at: {found_ip}")
        else:
            if not silent_startup:
                self.after(0, lambda: self.status_pill.configure(text="● PHONE NOT FOUND", text_color="#F87171"))
                self.log("Auto-detection finished: No phone found on subnet. Please enter IP manually.")

        self.after(0, lambda: self.btn_detect.configure(text="🔍 Auto-Detect", state="normal"))
        return found_ip


if __name__ == "__main__":
    app = P2PwireApp()
    app.mainloop()
