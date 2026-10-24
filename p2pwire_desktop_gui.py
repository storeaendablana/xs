# ==============================================================================
# P2Pwire Premium Desktop GUI Client (Cyberpunk Compact Edition)
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
import datetime

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

# HBSP Turbo 2.0 Constants
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

OP_DIRECT_START = 0x20
OP_DIRECT_ACK = 0x21
OP_DIRECT_COMPLETE = 0x22
OP_DIRECT_COMPLETE_ACK = 0x23

CHUNK_SIZE = 256 * 1024
DEFAULT_CHANNELS = 1

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
        self.title("P2Pwire Turbo Hub • Audio & 100+ MB/s Files")
        # Compact, space-saving dimensions
        self.geometry("540x590")
        self.minsize(500, 540)
        self.configure(fg_color=CLR_BG)

        self.is_streaming = False
        self.stream_lock = threading.Lock()
        self.stop_stream_event = threading.Event()
        self.stream_thread = None
        self.stream_loop = None

        self.is_scanning = False
        self.hbsp_cancel_flag = False
        self.is_transferring = False

        self.selected_file_path = ctk.StringVar(value="")
        self.transfer_progress = ctk.StringVar(value="Ready for Transfer")
        self.active_turbo_sessions = {}

        # Folders for Received and Sent files
        self.received_folder = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireTransfers")
        self.sent_folder = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireSent")
        os.makedirs(self.received_folder, exist_ok=True)
        os.makedirs(self.sent_folder, exist_ok=True)

        self.save_directory_path = ctk.StringVar(value=self.received_folder)

        self.anim_tick = 0
        self.waveform_levels = [0.0] * 38
        self.waveform_peaks = [0.0] * 38
        self.laser_pos = 0.0

        self.setup_header()
        self.setup_connection_bar()
        self.setup_tabs()

        self.log(f"[INIT] P2Pwire Desktop Turbo Hub Initialized.")
        self.log(f"[LOCAL PC IP] {self.get_pc_local_ip()} • Port 8080 (Sound) • Port 8082 (Files)")

        self.animate_loop()
        threading.Thread(target=self.detect_android_device, args=(True,), daemon=True).start()
        threading.Thread(target=self.run_pc_hbsp_receiver, daemon=True).start()

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        print(formatted.strip())
        if hasattr(self, 'txt_console') and self.txt_console:
            self.after(0, lambda: self._append_log_ui(formatted))

    def _append_log_ui(self, formatted):
        try:
            self.txt_console.configure(state="normal")
            self.txt_console.insert("end", formatted)
            self.txt_console.see("end")
            self.txt_console.configure(state="disabled")
        except Exception:
            pass

    def setup_header(self):
        header_card = ctk.CTkFrame(self, fg_color=CLR_CARD, corner_radius=10, border_width=1, border_color=CLR_CARD_BORDER)
        header_card.pack(fill="x", padx=10, pady=(8, 4))

        top_row = ctk.CTkFrame(header_card, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=6)

        title_box = ctk.CTkFrame(top_row, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="⚡ P2Pwire Turbo Hub",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=CLR_TEXT_BRIGHT
        ).pack(side="left")

        pc_ip = self.get_pc_local_ip()
        ctk.CTkLabel(
            title_box,
            text=f" • PC IP: {pc_ip}",
            font=ctk.CTkFont(size=11),
            text_color=CLR_ACCENT_CYAN
        ).pack(side="left", padx=(6, 0))

        self.status_pill = ctk.CTkLabel(
            top_row,
            text="● STANDBY",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=CLR_ACCENT_AMBER,
            fg_color="#1E293B",
            corner_radius=10,
            padx=10,
            pady=3
        )
        self.status_pill.pack(side="right")

    def setup_connection_bar(self):
        conn_frame = ctk.CTkFrame(self, fg_color=CLR_CARD, corner_radius=10, border_width=1, border_color=CLR_CARD_BORDER)
        conn_frame.pack(fill="x", padx=10, pady=3)

        input_row = ctk.CTkFrame(conn_frame, fg_color="transparent")
        input_row.pack(fill="x", padx=10, pady=6)

        ctk.CTkLabel(
            input_row,
            text="📱 Phone IP:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_TEXT_MUTED
        ).pack(side="left", padx=(0, 6))

        self.ip_entry = ctk.CTkEntry(
            input_row,
            placeholder_text="e.g. 192.168.43.1",
            height=32,
            corner_radius=6,
            font=ctk.CTkFont(size=12)
        )
        self.ip_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.ip_entry.insert(0, "192.168.43.1")

        self.btn_test = ctk.CTkButton(
            input_row,
            text="⚡ Test",
            command=self.test_connection,
            width=65,
            height=32,
            corner_radius=6,
            fg_color="#0284C7",
            hover_color="#0369A1",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.btn_test.pack(side="right", padx=(4, 0))

        self.btn_detect = ctk.CTkButton(
            input_row,
            text="🔍 Auto",
            command=self.start_auto_detect_thread,
            width=65,
            height=32,
            corner_radius=6,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.btn_detect.pack(side="right")

    def setup_tabs(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=CLR_CARD,
            segmented_button_fg_color="#0F172A",
            segmented_button_selected_color=CLR_ACCENT_CYAN,
            segmented_button_selected_hover_color="#0891B2",
            segmented_button_unselected_color="#1E293B",
            segmented_button_unselected_hover_color="#334155",
            corner_radius=10,
            border_width=1,
            border_color=CLR_CARD_BORDER
        )
        self.tabview.pack(fill="both", expand=True, padx=10, pady=(2, 6))

        self.tab_audio = self.tabview.add("🔊 Live Audio")
        self.tab_files = self.tabview.add("⚡ Fast Files")
        self.tab_console = self.tabview.add("📋 Event Log")

        self.setup_audio_tab_content()
        self.setup_files_tab_content()
        self.setup_console_tab_content()

    def setup_audio_tab_content(self):
        tab = self.tab_audio

        info_bar = ctk.CTkFrame(tab, fg_color="#0B132B", corner_radius=8, border_width=1, border_color="#1E293B")
        info_bar.pack(fill="x", padx=8, pady=(4, 4))
        ctk.CTkLabel(
            info_bar,
            text="🎵 Pure 48,000 Hz 16-Bit PCM • Hardware Loopback (Port 8080)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_ACCENT_CYAN
        ).pack(pady=4)

        self.canvas_visualizer = tk.Canvas(
            tab,
            width=500,
            height=110,
            bg="#0B0F19",
            highlightthickness=1,
            highlightbackground="#1E293B"
        )
        self.canvas_visualizer.pack(fill="x", padx=8, pady=4)

        self.bar_items = []
        self.peak_items = []
        bar_count = 38
        bar_w = 9
        gap = 4
        start_x = 8
        for i in range(bar_count):
            x1 = start_x + i * (bar_w + gap)
            x2 = x1 + bar_w
            b = self.canvas_visualizer.create_rectangle(x1, 55, x2, 55, fill="#1E293B", outline="")
            p = self.canvas_visualizer.create_line(x1, 55, x2, 55, fill="#38BDF8", width=2)
            self.bar_items.append(b)
            self.peak_items.append(p)

        self.canvas_hint = self.canvas_visualizer.create_text(
            250, 55,
            text="✦ Audio Engine Idle • Click Start to Stream to Phone Speaker",
            fill="#64748B",
            font=("Segoe UI", 10, "italic")
        )

        self.btn_toggle_stream = ctk.CTkButton(
            tab,
            text="▶ Start Streaming PC Audio to Phone Speaker",
            command=self.toggle_stream,
            height=40,
            corner_radius=8,
            fg_color=CLR_ACCENT_CYAN,
            hover_color="#0891B2",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.btn_toggle_stream.pack(fill="x", padx=8, pady=(8, 4))

    def setup_files_tab_content(self):
        tab = self.tab_files

        top_action_row = ctk.CTkFrame(tab, fg_color="transparent")
        top_action_row.pack(fill="x", padx=8, pady=(4, 2))

        ctk.CTkButton(
            top_action_row,
            text="📁 Select File",
            command=self.select_file,
            height=32,
            corner_radius=6,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            top_action_row,
            text="🚀 Send File",
            command=self.send_file_hbsp,
            height=32,
            corner_radius=6,
            fg_color=CLR_ACCENT_GREEN,
            hover_color="#059669",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=100
        ).pack(side="right")

        file_info_frame = ctk.CTkFrame(tab, fg_color="#0F172A", corner_radius=6, border_width=1, border_color="#1E293B")
        file_info_frame.pack(fill="x", padx=8, pady=(2, 4))

        self.lbl_selected_file = ctk.CTkLabel(
            file_info_frame,
            text="📁 No file selected • Click 'Select File' above",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=CLR_TEXT_MUTED,
            anchor="w"
        )
        self.lbl_selected_file.pack(fill="x", padx=8, pady=4)

        # Futuristic Cyberpunk Transfer Progress HUD
        self.canvas_progress = tk.Canvas(
            tab,
            width=500,
            height=34,
            bg="#0B0F19",
            highlightthickness=1,
            highlightbackground="#1E293B"
        )
        self.canvas_progress.pack(fill="x", padx=8, pady=2)
        self.progress_rect = self.canvas_progress.create_rectangle(0, 0, 0, 34, fill="#047857", outline="")
        self.laser_rect = self.canvas_progress.create_rectangle(0, 0, 0, 34, fill="#34D399", outline="", state="hidden")
        self.progress_text = self.canvas_progress.create_text(
            250, 17,
            text="Ready for HBSP Gigabit Transfer",
            fill="#E2E8F0",
            font=("Segoe UI", 10, "bold")
        )

        self.file_subtabview = ctk.CTkTabview(
            tab,
            fg_color="#0F172A",
            segmented_button_fg_color="#090D16",
            segmented_button_selected_color=CLR_ACCENT_GREEN,
            segmented_button_selected_hover_color="#059669",
            segmented_button_unselected_color="#1E293B",
            segmented_button_unselected_hover_color="#334155",
            corner_radius=8,
            border_width=1,
            border_color="#1E293B"
        )
        self.file_subtabview.pack(fill="both", expand=True, padx=8, pady=2)

        self.subtab_received = self.file_subtabview.add("📥 Received")
        self.subtab_sent = self.file_subtabview.add("📤 Sent")

        self.setup_received_list(self.subtab_received)
        self.setup_sent_list(self.subtab_sent)
        self.refresh_file_lists()

    def setup_console_tab_content(self):
        tab = self.tab_console
        action_row = ctk.CTkFrame(tab, fg_color="transparent")
        action_row.pack(fill="x", padx=6, pady=(2, 4))

        ctk.CTkLabel(
            action_row,
            text="Terminal & Connection Diagnostics Log",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_TEXT_MUTED
        ).pack(side="left")

        ctk.CTkButton(
            action_row,
            text="Clear Log",
            command=self.clear_console,
            width=70,
            height=24,
            corner_radius=4,
            fg_color="#1E293B",
            font=ctk.CTkFont(size=10)
        ).pack(side="right")

        self.txt_console = ctk.CTkTextbox(
            tab,
            fg_color="#080C14",
            text_color="#38BDF8",
            font=ctk.CTkFont(family="Consolas", size=10),
            corner_radius=6,
            border_width=1,
            border_color="#1E293B"
        )
        self.txt_console.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        self.txt_console.configure(state="disabled")

    def clear_console(self):
        if hasattr(self, 'txt_console') and self.txt_console:
            self.txt_console.configure(state="normal")
            self.txt_console.delete("1.0", "end")
            self.txt_console.configure(state="disabled")

    def setup_received_list(self, subtab):
        header_row = ctk.CTkFrame(subtab, fg_color="transparent")
        header_row.pack(fill="x", padx=4, pady=2)
        ctk.CTkLabel(header_row, text="Received Files (from Phone)", font=ctk.CTkFont(size=11, weight="bold"), text_color=CLR_TEXT_MUTED).pack(side="left")
        ctk.CTkButton(header_row, text="Open Folder", command=lambda: self.open_folder(self.received_folder), width=80, height=22, fg_color="#1E293B", font=ctk.CTkFont(size=9)).pack(side="right")

        self.received_scroll = ctk.CTkScrollableFrame(subtab, fg_color="transparent")
        self.received_scroll.pack(fill="both", expand=True, padx=2, pady=2)

    def setup_sent_list(self, subtab):
        header_row = ctk.CTkFrame(subtab, fg_color="transparent")
        header_row.pack(fill="x", padx=4, pady=2)
        ctk.CTkLabel(header_row, text="Sent Files (to Phone)", font=ctk.CTkFont(size=11, weight="bold"), text_color=CLR_TEXT_MUTED).pack(side="left")
        ctk.CTkButton(header_row, text="Open Folder", command=lambda: self.open_folder(self.sent_folder), width=80, height=22, fg_color="#1E293B", font=ctk.CTkFont(size=9)).pack(side="right")

        self.sent_scroll = ctk.CTkScrollableFrame(subtab, fg_color="transparent")
        self.sent_scroll.pack(fill="both", expand=True, padx=2, pady=2)

    def refresh_file_lists(self):
        if 'received_scroll' in self.__dict__:
            for widget in self.received_scroll.winfo_children():
                widget.destroy()
            try:
                files = sorted([os.path.join(self.received_folder, f) for f in os.listdir(self.received_folder)], key=os.path.getmtime, reverse=True)
                for fpath in files:
                    if os.path.isfile(fpath):
                        self.add_file_card(self.received_scroll, fpath)
            except Exception:
                pass

        if 'sent_scroll' in self.__dict__:
            for widget in self.sent_scroll.winfo_children():
                widget.destroy()
            try:
                files = sorted([os.path.join(self.sent_folder, f) for f in os.listdir(self.sent_folder)], key=os.path.getmtime, reverse=True)
                for fpath in files:
                    if os.path.isfile(fpath):
                        self.add_file_card(self.sent_scroll, fpath)
            except Exception:
                pass

    def add_file_card(self, parent, fpath):
        fname = os.path.basename(fpath)
        fsize = os.path.getsize(fpath) / (1024 * 1024)

        card = ctk.CTkFrame(parent, fg_color="#1E293B", corner_radius=6)
        card.pack(fill="x", padx=2, pady=2)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)

        info_box = ctk.CTkFrame(row, fg_color="transparent")
        info_box.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(info_box, text=fname, font=ctk.CTkFont(size=11, weight="bold"), text_color=CLR_TEXT_BRIGHT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(info_box, text=f"{fsize:.1f} MB", font=ctk.CTkFont(size=9), text_color=CLR_TEXT_MUTED, anchor="w").pack(anchor="w")

        ctk.CTkButton(
            row,
            text="▶ Open",
            command=lambda: self.open_file_path(fpath),
            width=65,
            height=24,
            corner_radius=4,
            fg_color=CLR_ACCENT_GREEN,
            hover_color="#059669",
            font=ctk.CTkFont(size=10, weight="bold")
        ).pack(side="right", padx=2)

        ctk.CTkButton(
            row,
            text="📁 Folder",
            command=lambda: self.open_folder(os.path.dirname(fpath)),
            width=55,
            height=24,
            corner_radius=4,
            fg_color="#334155",
            hover_color="#475569",
            font=ctk.CTkFont(size=10)
        ).pack(side="right")

    def open_file_path(self, fpath):
        try:
            if platform.system() == "Windows":
                os.startfile(fpath)
            elif platform.system() == "Darwin":
                subprocess.run(["open", fpath])
            else:
                subprocess.run(["xdg-open", fpath])
        except Exception as ex:
            self.log(f"[ERROR] Cannot open file: {ex}")

    def open_folder(self, dpath):
        if os.path.exists(dpath):
            try:
                if platform.system() == "Windows":
                    os.startfile(dpath)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", dpath])
                else:
                    subprocess.Popen(["xdg-open", dpath])
            except Exception as ex:
                self.log(f"[ERROR] Cannot open folder: {ex}")

    def get_pc_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            try:
                for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                    ip = info[4][0]
                    if not ip.startswith("127."):
                        return ip
            except Exception:
                pass
        return "127.0.0.1"

    def test_connection(self):
        ip = self.ip_entry.get().strip()
        if not ip:
            self.log("[TEST ERROR] Please enter Android Phone IP first.")
            return
        threading.Thread(target=self._run_connection_test, args=(ip,), daemon=True).start()

    def _run_connection_test(self, ip):
        self.log(f"[TEST] Testing connection to Phone at {ip}...")
        self.after(0, lambda: self.status_pill.configure(text=f"● TESTING...", text_color=CLR_ACCENT_CYAN))

        audio_ok = False
        try:
            s80 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s80.settimeout(1.2)
            res80 = s80.connect_ex((ip, 8080))
            s80.close()
            audio_ok = (res80 == 0)
        except Exception:
            pass

        files_ok = False
        try:
            s82 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s82.settimeout(1.2)
            res82 = s82.connect_ex((ip, 8082))
            s82.close()
            files_ok = (res82 == 0)
        except Exception:
            pass

        if audio_ok and files_ok:
            self.log(f"[TEST SUCCESS] ✓ Both Audio (8080) and Files (8082) REACHABLE on {ip}!")
            self.after(0, lambda: self.status_pill.configure(text=f"● REACHABLE ({ip})", text_color=CLR_ACCENT_GREEN))
        elif audio_ok:
            self.log(f"[TEST] ✓ Audio Port 8080 is reachable on {ip}. Ready for Sound Streaming!")
            self.after(0, lambda: self.status_pill.configure(text=f"● AUDIO READY ({ip})", text_color=CLR_ACCENT_CYAN))
        elif files_ok:
            self.log(f"[TEST] ✓ File Port 8082 is reachable on {ip}. Ready for File Transfer!")
            self.after(0, lambda: self.status_pill.configure(text=f"● FILES READY ({ip})", text_color=CLR_ACCENT_GREEN))
        else:
            self.log(f"[TEST FAILED] Cannot reach {ip}! Make sure Phone & PC are on the same Wi-Fi/Hotspot network.")
            self.after(0, lambda: self.status_pill.configure(text=f"● UNREACHABLE", text_color="#EF4444"))

    def animate_loop(self):
        self.anim_tick += 1
        if not self.is_streaming:
            self.canvas_visualizer.itemconfig(self.canvas_hint, state="normal")
            for i in range(len(self.bar_items)):
                wave = math.sin(self.anim_tick * 0.08 + i * 0.22) * 10 + 12
                self.canvas_visualizer.coords(self.bar_items[i], 8 + i * 13, 55 - wave, 8 + i * 13 + 9, 55 + wave)
                self.canvas_visualizer.itemconfig(self.bar_items[i], fill="#1E293B")
                self.canvas_visualizer.coords(self.peak_items[i], 8 + i * 13, 55 - wave - 2, 8 + i * 13 + 9, 55 - wave - 2)
                self.canvas_visualizer.itemconfig(self.peak_items[i], fill="#334155")
        else:
            self.canvas_visualizer.itemconfig(self.canvas_hint, state="hidden")
            for i in range(len(self.bar_items)):
                cur = self.waveform_levels[i]
                peak = self.waveform_peaks[i]
                peak = max(cur, peak - 1.2)
                self.waveform_peaks[i] = peak
                self.waveform_levels[i] = max(2.0, cur * 0.88)

                bar_color = CLR_ACCENT_CYAN if cur < 25 else CLR_ACCENT_GREEN
                x1 = 8 + i * 13
                x2 = x1 + 9
                self.canvas_visualizer.coords(self.bar_items[i], x1, 55 - cur, x2, 55 + cur)
                self.canvas_visualizer.itemconfig(self.bar_items[i], fill=bar_color)
                self.canvas_visualizer.coords(self.peak_items[i], x1, 55 - peak - 2, x2, 55 - peak - 2)
                self.canvas_visualizer.itemconfig(self.peak_items[i], fill="#38BDF8")

        if self.is_transferring:
            self.laser_pos = (self.laser_pos + 14) % 500
            self.canvas_progress.itemconfig(self.laser_rect, state="normal")
            self.canvas_progress.coords(self.laser_rect, self.laser_pos, 0, self.laser_pos + 26, 34)
        else:
            self.canvas_progress.itemconfig(self.laser_rect, state="hidden")

        self.after(30, self.animate_loop)

    def update_waveform_from_pcm(self, pcm_bytes):
        if not HAS_NUMPY or len(pcm_bytes) < 76:
            return
        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            chunk_len = len(samples) // 38
            if chunk_len == 0:
                return
            for i in range(38):
                chunk = samples[i * chunk_len : (i + 1) * chunk_len]
                amp = (np.abs(chunk).mean() / 32768.0) * 52.0
                if amp > self.waveform_levels[i]:
                    self.waveform_levels[i] = min(54.0, amp)
        except Exception:
            pass

    def toggle_stream(self):
        if not self.is_streaming:
            ip = self.ip_entry.get().strip()
            if not ip:
                self.log("[AUDIO ERROR] Please enter Android IP address.")
                return

            with self.stream_lock:
                self.is_streaming = True
                self.stop_stream_event.clear()
                self.btn_toggle_stream.configure(text="⏹ Stop Streaming Audio", fg_color="#EF4444", hover_color="#DC2626")
                self.status_pill.configure(text="● CONNECTING...", text_color=CLR_ACCENT_CYAN)
                self.log(f"[AUDIO] Connecting to ws://{ip}:8080/ws/upload_audio...")
                self.stream_thread = threading.Thread(target=self.run_audio_worker, args=(ip,), daemon=True)
                self.stream_thread.start()
        else:
            self.stop_streaming_cleanly(final_status="● STANDBY", status_color=CLR_ACCENT_AMBER)
            self.log("[AUDIO] Audio streaming stopped by user.")

    def stop_streaming_cleanly(self, final_status=None, status_color=None):
        with self.stream_lock:
            self.is_streaming = False
            self.stop_stream_event.set()
            self.btn_toggle_stream.configure(text="▶ Start Streaming PC Audio to Phone Speaker", fg_color=CLR_ACCENT_CYAN, hover_color="#0891B2")
            if final_status:
                self.status_pill.configure(text=final_status, text_color=status_color or CLR_ACCENT_AMBER)
            else:
                self.status_pill.configure(text="● STANDBY", text_color=CLR_ACCENT_AMBER)

    def run_audio_worker(self, ip):
        async def stream():
            uri = f"ws://{ip}:8080/ws/upload_audio"
            ws = None
            try:
                ws = await websockets.connect(uri, ping_interval=20, ping_timeout=15, max_size=None)
                self.after(0, lambda: self.status_pill.configure(text="● STREAMING LIVE", text_color=CLR_ACCENT_GREEN))
                self.log(f"[AUDIO SUCCESS] Connected! Streaming 48kHz PCM audio to Phone ({ip}).")

                if not HAS_SOUNDCARD:
                    raise RuntimeError("soundcard library not found. Run: pip install soundcard")

                speaker = sc.default_speaker()
                mic = sc.get_microphone(id=speaker.name, include_loopback=True)
                audio_queue = asyncio.Queue(maxsize=20)
                capture_stop = threading.Event()

                def capture_task():
                    try:
                        with mic.recorder(samplerate=48000, channels=2) as recorder:
                            while self.is_streaming and not capture_stop.is_set():
                                data = recorder.record(numframes=960)
                                mono = np.mean(data, axis=1)
                                mono = np.clip(mono, -1.0, 1.0)
                                pcm16 = (mono * 32767).astype(np.int16).tobytes()
                                if audio_queue.full():
                                    try: audio_queue.get_nowait()
                                    except: pass
                                self.stream_loop.call_soon_threadsafe(audio_queue.put_nowait, pcm16)
                    except Exception as ce:
                        self.log(f"[CAPTURE WARNING] {ce}")

                threading.Thread(target=capture_task, daemon=True).start()

                seq = 0
                while self.is_streaming and not self.stop_stream_event.is_set():
                    try:
                        pcm16 = await asyncio.wait_for(audio_queue.get(), timeout=1.2)
                    except asyncio.TimeoutError:
                        pcm16 = bytes(960 * 2)

                    h = bytearray(8)
                    h[0], h[1], h[2] = 0xAB, 0x46, 0x01
                    h[3], h[4] = (seq >> 8) & 0xFF, seq & 0xFF
                    seq = (seq + 1) & 0xFFFF

                    await ws.send(bytes(h) + pcm16)
                    self.after(0, self.update_waveform_from_pcm, pcm16)
                capture_stop.set()
            except Exception as e:
                err_msg = str(e)
                self.log(f"[AUDIO ERROR] Connection failed: {err_msg}")
                self.log("💡 Tip: Verify Phone IP is correct and P2Pwire app is open on the Phone.")
                self.after(0, lambda: self.stop_streaming_cleanly(final_status="● DISCONNECTED", status_color="#EF4444"))
                return
            finally:
                if ws:
                    try: await ws.close()
                    except: pass
            self.after(0, lambda: self.stop_streaming_cleanly(final_status="● STANDBY", status_color=CLR_ACCENT_AMBER))

        self.stream_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.stream_loop)
        try:
            self.stream_loop.run_until_complete(stream())
        except:
            pass

    def select_file(self):
        file = filedialog.askopenfilename()
        if file:
            size_mb = os.path.getsize(file) / (1024 * 1024)
            name = os.path.basename(file)
            self.selected_file_path.set(f"{name} ({size_mb:.1f} MB)")
            self._current_file_path = file
            self.lbl_selected_file.configure(
                text=f"✓ Selected: {name} ({size_mb:.1f} MB) • Ready to Send",
                text_color=CLR_ACCENT_CYAN
            )
            self.log(f"[FILE] Selected: {name} ({size_mb:.1f} MB)")

    def update_transfer_ui(self, progress, status_text, speed_mbps=0.0):
        bar_width = int(progress * 500)
        self.canvas_progress.coords(self.progress_rect, 0, 0, bar_width, 34)
        pct_label = f"⚡ {int(progress * 100)}% • {status_text} • {speed_mbps:.1f} MB/s"
        self.canvas_progress.itemconfig(self.progress_text, text=pct_label)
        self.transfer_progress.set(status_text)

    def recv_exact(self, sock, num_bytes):
        data = bytearray()
        while len(data) < num_bytes:
            packet = sock.recv(num_bytes - len(data))
            if not packet: raise EOFError("Connection closed by peer")
            data.extend(packet)
        return bytes(data)

    def send_file_hbsp(self):
        ip = self.ip_entry.get().strip()
        file_path = getattr(self, '_current_file_path', None)
        if not file_path or not os.path.exists(file_path):
            self.lbl_selected_file.configure(
                text="⚠️ Please click 'Select File' first!",
                text_color=CLR_ACCENT_AMBER
            )
            self.log("[FILE ERROR] No file selected to send.")
            return
        if not ip:
            self.log("[FILE ERROR] Phone IP is empty. Please enter Phone IP.")
            self.update_transfer_ui(0.0, "Error: Android Phone IP is empty!", 0.0)
            return
        threading.Thread(target=self.perform_hbsp_turbo_upload, args=(ip, file_path), daemon=True).start()

    def perform_hbsp_turbo_upload(self, ip, file_path):
        self.is_transferring = True
        master_sock = None
        try:
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)
            name_bytes = file_name.encode('utf-8')
            total_mb = file_size / (1024 * 1024)

            try:
                dest_sent = os.path.join(self.sent_folder, file_name)
                if not os.path.exists(dest_sent) or os.path.abspath(file_path) != os.path.abspath(dest_sent):
                    import shutil
                    shutil.copy2(file_path, dest_sent)
                self.after(0, self.refresh_file_lists)
            except: pass

            self.log(f"[FILE TRANSFER] Connecting to HBSP Server at {ip}:8082 for {file_name} ({total_mb:.1f} MB)...")
            self.after(0, lambda: self.update_transfer_ui(0.01, f"Connecting to {ip}:8082...", 0.0))

            master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            master_sock.settimeout(12.0)
            master_sock.connect((ip, 8082))

            # Packet: [MAGIC: 4][VERSION: 1][OP_DIRECT_START: 1][FILE_SIZE: 8][NAME_LEN: 2][NAME_BYTES: N]
            init_header = bytearray()
            init_header.extend(HBSP_MAGIC)
            init_header.append(HBSP_VERSION)
            init_header.append(OP_DIRECT_START)
            init_header.extend(struct.pack('>Q', file_size))
            init_header.extend(struct.pack('>H', len(name_bytes)))
            init_header.extend(name_bytes)
            master_sock.sendall(init_header)

            ack_data = self.recv_exact(master_sock, 7)
            if not ack_data.startswith(HBSP_MAGIC) or ack_data[5] != OP_DIRECT_ACK or ack_data[6] != 0:
                raise Exception("Phone rejected transfer handshake")

            self.log(f"[FILE TRANSFER] Handshake OK! Streaming {file_name} via Direct High-Speed Pipeline...")

            start_time = time.time()
            last_calc_time = start_time
            bytes_since_calc = 0
            uploaded = 0
            speed = 0.0
            buf_size = 512 * 1024

            with open(file_path, 'rb') as f:
                while uploaded < file_size:
                    chunk = f.read(buf_size)
                    if not chunk: break
                    master_sock.sendall(chunk)
                    uploaded += len(chunk)
                    bytes_since_calc += len(chunk)

                    now = time.time()
                    diff = now - last_calc_time
                    if diff >= 0.2:
                        speed = (bytes_since_calc / (1024 * 1024)) / diff
                        pct = uploaded / file_size if file_size > 0 else 0
                        up_mb = uploaded / (1024 * 1024)
                        rem_mb = max(0, total_mb - up_mb)
                        eta = int(rem_mb / speed) if speed > 0.1 else 0
                        status_str = f"{up_mb:.1f}/{total_mb:.1f} MB (ETA {eta}s)"
                        self.after(0, lambda p=pct, s=status_str, sp=speed: self.update_transfer_ui(p, s, sp))
                        last_calc_time = now
                        bytes_since_calc = 0

            # Send OP_DIRECT_COMPLETE
            master_sock.sendall(bytes([OP_DIRECT_COMPLETE]))
            comp_ack = self.recv_exact(master_sock, 7)

            total_time = max(0.1, time.time() - start_time)
            avg_speed = (file_size / (1024 * 1024)) / total_time
            self.log(f"[FILE SUCCESS] ✓ Sent {file_name} ({total_mb:.1f} MB) in {total_time:.1f}s (Avg: {avg_speed:.1f} MB/s)!")
            self.after(0, lambda: self.update_transfer_ui(1.0, f"Complete in {total_time:.1f}s!", avg_speed))
            self.after(0, self.refresh_file_lists)
        except Exception as e:
            self.log(f"[TRANSFER ERROR] {e}")
            self.after(0, lambda: self.update_transfer_ui(0.0, f"Error: {e}", 0.0))
        finally:
            self.is_transferring = False
            if master_sock:
                try: master_sock.close()
                except: pass

    def run_pc_hbsp_receiver(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", 8082))
            srv.listen(10)
            self.log("[RECEIVER] PC HBSP File Server listening on 0.0.0.0:8082")
            while True:
                conn, addr = srv.accept()
                threading.Thread(target=self.handle_incoming_connection_on_pc, args=(conn, addr), daemon=True).start()
        except Exception as ex:
            self.log(f"[RECEIVER ERROR] Cannot bind HBSP Port 8082: {ex}")

    def handle_incoming_connection_on_pc(self, sock, addr):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(35.0)
            magic = self.recv_exact(sock, 4)
            if magic != HBSP_MAGIC:
                sock.close()
                return
            ver = sock.recv(1)[0]
            op = sock.recv(1)[0]

            if op == OP_DIRECT_START:
                file_size = struct.unpack('>Q', self.recv_exact(sock, 8))[0]
                name_len = struct.unpack('>H', self.recv_exact(sock, 2))[0]
                file_name = self.recv_exact(sock, name_len).decode('utf-8')
                total_mb = file_size / (1024 * 1024)

                out_path = os.path.join(self.received_folder, file_name)
                self.log(f"[RECEIVE] Incoming file from Phone ({addr[0]}): {file_name} ({total_mb:.1f} MB)...")

                # Send OP_DIRECT_ACK: [MAGIC: 4][VER: 1][OP_DIRECT_ACK: 1][STATUS: 0]
                ack = bytearray()
                ack.extend(HBSP_MAGIC)
                ack.append(HBSP_VERSION)
                ack.append(OP_DIRECT_ACK)
                ack.append(0)
                sock.sendall(ack)

                self.is_transferring = True
                start_time = time.time()
                last_calc_time = start_time
                bytes_since_calc = 0
                received = 0
                speed = 0.0
                buf_size = 512 * 1024

                with open(out_path, 'wb') as f:
                    while received < file_size:
                        to_read = min(buf_size, file_size - received)
                        chunk = sock.recv(to_read)
                        if not chunk: break
                        f.write(chunk)
                        received += len(chunk)
                        bytes_since_calc += len(chunk)

                        now = time.time()
                        diff = now - last_calc_time
                        if diff >= 0.2:
                            speed = (bytes_since_calc / (1024 * 1024)) / diff
                            pct = received / file_size if file_size > 0 else 0
                            rec_mb = received / (1024 * 1024)
                            rem_mb = max(0, total_mb - rec_mb)
                            eta = int(rem_mb / speed) if speed > 0.1 else 0
                            status_str = f"📥 {rec_mb:.1f}/{total_mb:.1f} MB (ETA {eta}s)"
                            self.after(0, lambda p=pct, s=status_str, sp=speed: self.update_transfer_ui(p, s, sp))
                            last_calc_time = now
                            bytes_since_calc = 0

                # Read OP_DIRECT_COMPLETE
                comp_op = sock.recv(1)

                cack = bytearray()
                cack.extend(HBSP_MAGIC)
                cack.append(HBSP_VERSION)
                cack.append(OP_DIRECT_COMPLETE_ACK)
                cack.append(0)
                sock.sendall(cack)

                total_time = max(0.1, time.time() - start_time)
                avg_speed = (file_size / (1024 * 1024)) / total_time
                self.log(f"[RECEIVE SUCCESS] ✓ Received {file_name} in {total_time:.1f}s ({avg_speed:.1f} MB/s) -> Saved in Downloads/P2PwireTransfers")
                self.after(0, lambda: self.update_transfer_ui(1.0, f"Complete in {total_time:.1f}s!", avg_speed))
                self.after(0, self.refresh_file_lists)
                self.is_transferring = False

            elif op == OP_TURBO_INIT:
                session_id = struct.unpack('>I', self.recv_exact(sock, 4))[0]
                file_size = struct.unpack('>Q', self.recv_exact(sock, 8))[0]
                channels_cnt = sock.recv(1)[0]
                name_len = struct.unpack('>H', self.recv_exact(sock, 2))[0]
                file_name = self.recv_exact(sock, name_len).decode('utf-8')

                out_path = os.path.join(self.received_folder, file_name)
                with open(out_path, 'wb') as f:
                    f.truncate(file_size)
                self.active_turbo_sessions[session_id] = out_path

                ack = bytearray()
                ack.extend(HBSP_MAGIC)
                ack.append(HBSP_VERSION)
                ack.append(OP_TURBO_INIT_ACK)
                ack.extend(struct.pack('>I', session_id))
                ack.append(0)
                sock.sendall(ack)

                sock.recv(1)
                self.active_turbo_sessions.pop(session_id, None)

                cack = bytearray()
                cack.extend(HBSP_MAGIC)
                cack.append(HBSP_VERSION)
                cack.append(OP_TURBO_COMPLETE_ACK)
                cack.append(0)
                sock.sendall(cack)
                self.after(0, self.refresh_file_lists)

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

                if not target_path:
                    recent = sorted([os.path.join(self.received_folder, f) for f in os.listdir(self.received_folder)], key=os.path.getmtime, reverse=True)
                    if recent: target_path = recent[0]

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
                                if not n: break
                                filled += n
                            if filled == 0: break
                            f.write(mv[:filled])
                            received += filled
        except Exception as ex:
            self.log(f"[RECEIVE WARNING] {ex}")
        finally:
            self.is_transferring = False
            try: sock.close()
            except: pass

    def probe_ip(self, ip):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.18)
            res = sock.connect_ex((ip, 8080))
            sock.close()
            if res == 0: return ip
        except:
            pass
        return None

    def start_auto_detect_thread(self):
        if self.is_scanning: return
        threading.Thread(target=self.detect_android_device, daemon=True).start()

    def detect_android_device(self, silent_startup=False):
        self.is_scanning = True
        if not silent_startup:
            self.log("[SCAN] Auto-detecting Android device on local network...")
            self.after(0, lambda: self.status_pill.configure(text="● SCANNING...", text_color=CLR_ACCENT_CYAN))

        found_ip = None
        candidates = []

        # Check default gateway from OS route
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

        for gw in ["192.168.43.1", "192.168.44.1", "192.168.50.1", "192.168.1.1", "192.168.0.1"]:
            if gw not in candidates:
                candidates.append(gw)

        for cand in candidates:
            if self.probe_ip(cand):
                found_ip = cand
                break

        # Fast parallel subnet probe if not found in gateways
        if not found_ip and not silent_startup:
            try:
                local_ip = self.get_pc_local_ip()
                if not local_ip.startswith("127."):
                    prefix = ".".join(local_ip.split(".")[:3])
                    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as executor:
                        futures = [executor.submit(self.probe_ip, f"{prefix}.{i}") for i in range(1, 255)]
                        for f in concurrent.futures.as_completed(futures):
                            r = f.result()
                            if r:
                                found_ip = r
                                break
            except Exception:
                pass

        self.is_scanning = False
        if found_ip:
            self.log(f"[SCAN SUCCESS] Found Android Device at {found_ip}!")
            self.after(0, lambda: self.ip_entry.delete(0, "end"))
            self.after(0, lambda: self.ip_entry.insert(0, found_ip))
            self.after(0, lambda: self.status_pill.configure(text=f"● FOUND ({found_ip})", text_color=CLR_ACCENT_GREEN))
        else:
            if not silent_startup:
                self.log("[SCAN] No active Android device responded. Please enter the Phone's IP manually.")
                self.after(0, lambda: self.status_pill.configure(text="● STANDBY", text_color=CLR_ACCENT_AMBER))


if __name__ == "__main__":
    app = P2PwireApp()
    app.mainloop()
