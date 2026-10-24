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

# HBSP Turbo 2.0 Protocol Constants
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

        self.received_folder = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireTransfers")
        self.sent_folder = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireSent")
        os.makedirs(self.received_folder, exist_ok=True)
        os.makedirs(self.sent_folder, exist_ok=True)

        self.save_directory_path = ctk.StringVar(value=self.received_folder)

        self.anim_tick = 0
        self.waveform_levels = [0.0] * 38
        self.waveform_peaks = [0.0] * 38

        # Progress bar animation state
        self.transfer_pct = 0.0
        self.transfer_status_text = "HBSP GIGABIT PIPELINE READY • WAITING FOR FILE"
        self.transfer_speed_mbps = 0.0
        self.transfer_mode = "idle"  # "idle", "sending", "receiving", "success", "error"
        self.transfer_eta = 0
        self.stripe_offset = 0.0

        self.pc_ip = self.get_pc_local_ip()

        self.setup_header()
        self.setup_connection_bar()
        self.setup_tabs()

        self.log("=" * 60)
        self.log(f"[INIT] P2Pwire Desktop Turbo Hub Initialized.")
        self.log(f"[LOCAL PC IP] {self.pc_ip} • Port 8080 (Sound) • Port 8082 (Files)")

        if not self.pc_ip.startswith("127."):
            prefix = ".".join(self.pc_ip.split(".")[:3])
            if prefix == "192.168.43":
                self.log(f"[NETWORK] Connected to Android Hotspot subnet ({prefix}.x). Default Phone IP: 192.168.43.1")
            else:
                self.log(f"[NETWORK] PC is on Wi-Fi subnet {prefix}.0/24 (IP: {self.pc_ip}).")
                self.log(f"💡 TIP: Check your Phone's P2Pwire screen for its IP ({prefix}.X) and enter it above.")

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

        ctk.CTkLabel(
            title_box,
            text=f" • PC IP: {self.pc_ip}",
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

        prefix = ".".join(self.pc_ip.split(".")[:3])
        default_ip = "192.168.43.1" if prefix == "192.168.43" else f"{prefix}."

        self.ip_entry = ctk.CTkEntry(
            input_row,
            placeholder_text=f"e.g. {prefix}.X (see Phone screen)",
            height=32,
            corner_radius=6,
            font=ctk.CTkFont(size=12)
        )
        self.ip_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        if default_ip:
            self.ip_entry.insert(0, default_ip)

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
            text="🔊 48kHz Direct Audio Stream • PC Soundcard -> Phone Speaker",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=CLR_ACCENT_CYAN
        ).pack(pady=4)

        self.canvas_visualizer = tk.Canvas(
            tab,
            width=500,
            height=110,
            bg="#070A12",
            highlightthickness=1,
            highlightbackground="#1E293B"
        )
        self.canvas_visualizer.pack(fill="x", padx=8, pady=4)

        self.bar_items = []
        self.peak_items = []
        for i in range(38):
            x1 = 8 + i * 13
            x2 = x1 + 9
            bar = self.canvas_visualizer.create_rectangle(x1, 55, x2, 55, fill="#1E293B", outline="")
            peak = self.canvas_visualizer.create_rectangle(x1, 53, x2, 55, fill="#334155", outline="")
            self.bar_items.append(bar)
            self.peak_items.append(peak)

        self.canvas_hint = self.canvas_visualizer.create_text(
            250, 55,
            text="Standby Sine Wave • Audio Stream Inactive",
            fill="#475569",
            font=("Segoe UI", 10, "bold")
        )

        vol_frame = ctk.CTkFrame(tab, fg_color="transparent")
        vol_frame.pack(fill="x", padx=8, pady=(2, 2))
        ctk.CTkLabel(
            vol_frame,
            text="Phone Speaker Buffer Depth: Ultra Low Latency (40ms)",
            font=ctk.CTkFont(size=10),
            text_color=CLR_TEXT_MUTED
        ).pack(side="left")

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
            text="📁 Pick File",
            command=self.select_file,
            height=34,
            corner_radius=6,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=90
        ).pack(side="left", padx=(0, 6))

        # True 1-Click Fast Send Button
        self.btn_turbo_send = ctk.CTkButton(
            top_action_row,
            text="🚀 1-Click Fast Send to Phone (HBSP)",
            command=self.send_file_hbsp,
            height=34,
            corner_radius=6,
            fg_color=CLR_ACCENT_GREEN,
            hover_color="#059669",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_turbo_send.pack(side="left", fill="x", expand=True)

        file_info_frame = ctk.CTkFrame(tab, fg_color="#0F172A", corner_radius=6, border_width=1, border_color="#1E293B")
        file_info_frame.pack(fill="x", padx=8, pady=(2, 4))

        self.lbl_selected_file = ctk.CTkLabel(
            file_info_frame,
            text="📁 Ready • Click '1-Click Fast Send' to transfer any file instantly",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=CLR_TEXT_MUTED,
            anchor="w"
        )
        self.lbl_selected_file.pack(fill="x", padx=8, pady=4)

        # Premium Cyberpunk Holographic HUD Progress Bar
        progress_hud_container = ctk.CTkFrame(tab, fg_color="#0B101E", corner_radius=8, border_width=1, border_color="#1E293B")
        progress_hud_container.pack(fill="x", padx=8, pady=2)

        hud_header = ctk.CTkFrame(progress_hud_container, fg_color="transparent")
        hud_header.pack(fill="x", padx=8, pady=(4, 2))

        self.lbl_hud_status = ctk.CTkLabel(
            hud_header,
            text="● HBSP TURBO READY",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=CLR_ACCENT_CYAN
        )
        self.lbl_hud_status.pack(side="left")

        self.lbl_hud_speed = ctk.CTkLabel(
            hud_header,
            text="Speed: 0.0 MB/s • ETA: --",
            font=ctk.CTkFont(size=10, family="Consolas"),
            text_color=CLR_TEXT_MUTED
        )
        self.lbl_hud_speed.pack(side="right")

        self.canvas_progress = tk.Canvas(
            progress_hud_container,
            width=500,
            height=38,
            bg="#070B14",
            highlightthickness=0
        )
        self.canvas_progress.pack(fill="x", padx=8, pady=(2, 6))

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
        if not ip or ip.endswith("."):
            self.log("[TEST ERROR] Please enter a valid Android Phone IP address.")
            return
        threading.Thread(target=self._run_connection_test, args=(ip,), daemon=True).start()

    def _run_connection_test(self, ip):
        self.log(f"[TEST] Testing connection to Phone at {ip}...")
        self.after(0, lambda: self.status_pill.configure(text=f"● TESTING...", text_color=CLR_ACCENT_CYAN))

        pc_ip = self.get_pc_local_ip()
        pc_prefix = ".".join(pc_ip.split(".")[:3])
        target_prefix = ".".join(ip.split(".")[:3])

        if pc_prefix != target_prefix and not ip.startswith("127."):
            self.log(f"⚠️ [SUBNET WARNING] PC is on {pc_ip} but target IP is {ip}!")
            self.log(f"   Different subnets ({pc_prefix}.x vs {target_prefix}.x) cannot communicate directly.")
            self.log(f"   👉 If phone is on this Wi-Fi, look at Phone app screen for its IP: {pc_prefix}.X")
            self.log(f"   👉 If using Phone Hotspot, connect PC Wi-Fi to the Phone Hotspot first!")

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

        self.draw_cyber_progress_bar()
        self.after(30, self.animate_loop)

    def draw_cyber_progress_bar(self):
        try:
            w = self.canvas_progress.winfo_width()
            if w <= 10:
                w = 500
            h = 38

            self.canvas_progress.delete("all")
            self.canvas_progress.create_rectangle(1, 1, w - 1, h - 1, fill="#080E1A", outline="#1E293B", width=1)

            for pct in (0.25, 0.50, 0.75):
                gx = int(w * pct)
                self.canvas_progress.create_line(gx, 4, gx, h - 4, fill="#131C31", width=1)

            if self.transfer_mode in ("sending", "receiving"):
                prog_w = max(6, int(w * self.transfer_pct))
                is_send = (self.transfer_mode == "sending")
                fill_color = "#0284C7" if is_send else "#059669"
                glow_color = "#38BDF8" if is_send else "#34D399"

                self.canvas_progress.create_rectangle(2, 2, prog_w, h - 2, fill=fill_color, outline="")

                self.stripe_offset = (self.stripe_offset + 3.0) % 24
                for sx in range(int(-24 + self.stripe_offset), prog_w + 24, 18):
                    p1_x = max(2, min(prog_w, sx))
                    p2_x = max(2, min(prog_w, sx + 8))
                    if p2_x > 2 and p1_x < prog_w:
                        self.canvas_progress.create_polygon(
                            p1_x, h - 2,
                            min(prog_w, p1_x + 6), 2,
                            p2_x, 2,
                            max(2, p2_x - 6), h - 2,
                            fill=glow_color, outline=""
                        )

                head_x = min(w - 3, prog_w)
                self.canvas_progress.create_line(head_x, 2, head_x, h - 2, fill="#FFFFFF", width=3)
                self.canvas_progress.create_line(max(2, head_x - 4), h // 2, min(w - 2, head_x + 4), h // 2, fill="#FFFFFF", width=7)

                pct_label = f"{int(self.transfer_pct * 100)}%"
                direction_icon = "📤 Sending" if is_send else "📥 Receiving"
                txt = f"{direction_icon} {pct_label} • {self.transfer_status_text}"
                self.canvas_progress.create_text(w // 2 + 1, h // 2 + 1, text=txt, fill="#020617", font=("Segoe UI", 10, "bold"))
                self.canvas_progress.create_text(w // 2, h // 2, text=txt, fill="#F8FAFC", font=("Segoe UI", 10, "bold"))

            elif self.transfer_mode == "success":
                self.canvas_progress.create_rectangle(2, 2, w - 2, h - 2, fill="#047857", outline="#10B981")
                txt = f"✓ 100% • {self.transfer_status_text} • {self.transfer_speed_mbps:.1f} MB/s"
                self.canvas_progress.create_text(w // 2 + 1, h // 2 + 1, text=txt, fill="#020617", font=("Segoe UI", 10, "bold"))
                self.canvas_progress.create_text(w // 2, h // 2, text=txt, fill="#F8FAFC", font=("Segoe UI", 10, "bold"))

            elif self.transfer_mode == "error":
                self.canvas_progress.create_rectangle(2, 2, w - 2, h - 2, fill="#7F1D1D", outline="#EF4444")
                txt = f"⚠️ {self.transfer_status_text}"
                self.canvas_progress.create_text(w // 2, h // 2, text=txt, fill="#FEE2E2", font=("Segoe UI", 10, "bold"))

            else:
                beam_x = int((math.sin(self.anim_tick * 0.05) + 1.0) * 0.5 * (w - 70)) + 35
                beam_w = 70
                x1 = max(2, beam_x - beam_w // 2)
                x2 = min(w - 2, beam_x + beam_w // 2)

                self.canvas_progress.create_rectangle(x1, 3, x2, h - 3, fill="#0C2036", outline="")
                self.canvas_progress.create_line(beam_x, 3, beam_x, h - 3, fill="#06B6D4", width=2)

                pulse = math.sin(self.anim_tick * 0.08) * 0.5 + 0.5
                color = "#38BDF8" if pulse > 0.4 else "#06B6D4"
                idle_txt = "⚡ HBSP GIGABIT PIPELINE READY • 100+ MB/s WAITING FOR FILE"
                self.canvas_progress.create_text(w // 2, h // 2, text=idle_txt, fill=color, font=("Segoe UI", 10, "bold"))

        except Exception:
            pass

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

    def get_loopback_device(self):
        """Discovers Windows WASAPI Loopback device safely without triggering 0x80070490."""
        if not HAS_SOUNDCARD:
            return None

        # Method A: Search all microphones with isloopback == True
        try:
            mics = sc.all_microphones(include_loopback=True)
            spk = None
            try:
                spk = sc.default_speaker()
            except Exception:
                pass

            if spk:
                for m in mics:
                    if getattr(m, 'isloopback', False):
                        if getattr(m, 'name', '') == getattr(spk, 'name', ''):
                            return m
                        if hasattr(spk, 'id') and getattr(spk, 'id', '') and getattr(spk, 'id', '') in getattr(m, 'id', ''):
                            return m

            for m in mics:
                if getattr(m, 'isloopback', False):
                    return m
        except Exception as ex:
            self.log(f"[AUDIO] Device scan notice: {ex}")

        # Method B: Lookup by default speaker ID (NOT name!)
        try:
            spk = sc.default_speaker()
            if spk and hasattr(spk, 'id') and spk.id:
                return sc.get_microphone(id=spk.id, include_loopback=True)
        except Exception:
            pass

        # Method C: Default microphone fallback
        try:
            return sc.default_microphone()
        except Exception:
            pass

        return None

    def toggle_stream(self):
        if not self.is_streaming:
            ip = self.ip_entry.get().strip()
            if not ip or ip.endswith("."):
                self.log("[AUDIO ERROR] Please enter a valid Android IP address.")
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

                mic = self.get_loopback_device()
                if mic:
                    self.log(f"[AUDIO] Capturing from loopback device: {getattr(mic, 'name', 'Speaker Loopback')}")
                else:
                    self.log("⚠️ [AUDIO NOTICE] Direct loopback device not found; using fallback audio streamer.")

                audio_queue = asyncio.Queue(maxsize=20)
                capture_stop = threading.Event()

                def capture_task():
                    try:
                        if mic:
                            with mic.recorder(samplerate=48000, channels=2) as recorder:
                                while self.is_streaming and not capture_stop.is_set():
                                    try:
                                        data = recorder.record(numframes=960)
                                        mono = np.mean(data, axis=1)
                                        mono = np.clip(mono, -1.0, 1.0)
                                        pcm16 = (mono * 32767).astype(np.int16).tobytes()
                                    except Exception:
                                        pcm16 = bytes(960 * 2)

                                    if audio_queue.full():
                                        try: audio_queue.get_nowait()
                                        except: pass
                                    self.stream_loop.call_soon_threadsafe(audio_queue.put_nowait, pcm16)
                        else:
                            while self.is_streaming and not capture_stop.is_set():
                                time.sleep(0.02)
                                pcm16 = bytes(960 * 2)
                                if audio_queue.full():
                                    try: audio_queue.get_nowait()
                                    except: pass
                                self.stream_loop.call_soon_threadsafe(audio_queue.put_nowait, pcm16)
                    except Exception as ce:
                        self.log(f"[AUDIO NOTICE] PC Audio Endpoint message: {ce}")
                        self.log("💡 Tip: Start playing any music/video on PC so Windows activates speaker loopback.")
                        while self.is_streaming and not capture_stop.is_set():
                            time.sleep(0.02)
                            pcm16 = bytes(960 * 2)
                            if audio_queue.full():
                                try: audio_queue.get_nowait()
                                except: pass
                            self.stream_loop.call_soon_threadsafe(audio_queue.put_nowait, pcm16)

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

    def update_transfer_ui(self, progress, status_text, speed_mbps=0.0, mode=None, eta=0):
        self.transfer_pct = max(0.0, min(1.0, progress))
        self.transfer_status_text = status_text
        self.transfer_speed_mbps = speed_mbps
        self.transfer_eta = eta

        if mode:
            self.transfer_mode = mode
        elif progress >= 1.0:
            self.transfer_mode = "success"
        elif progress > 0.0:
            self.transfer_mode = "sending" if "Sent" in status_text or "Send" in status_text or "up" in status_text.lower() else "receiving"
        else:
            self.transfer_mode = "idle"

        if self.transfer_mode == "sending":
            self.lbl_hud_status.configure(text="🚀 TRANSMITTING (HBSP)", text_color=CLR_ACCENT_CYAN)
            self.lbl_hud_speed.configure(text=f"⚡ {speed_mbps:.1f} MB/s • ETA: {eta}s", text_color=CLR_ACCENT_GREEN)
        elif self.transfer_mode == "receiving":
            self.lbl_hud_status.configure(text="📥 RECEIVING (HBSP)", text_color=CLR_ACCENT_GREEN)
            self.lbl_hud_speed.configure(text=f"⚡ {speed_mbps:.1f} MB/s • ETA: {eta}s", text_color=CLR_ACCENT_CYAN)
        elif self.transfer_mode == "success":
            self.lbl_hud_status.configure(text="✓ COMPLETED", text_color=CLR_ACCENT_GREEN)
            self.lbl_hud_speed.configure(text=f"⚡ {speed_mbps:.1f} MB/s", text_color=CLR_ACCENT_GREEN)
        elif self.transfer_mode == "error":
            self.lbl_hud_status.configure(text="⚠️ ERROR", text_color="#EF4444")
            self.lbl_hud_speed.configure(text="Transfer failed", text_color="#EF4444")
        else:
            self.lbl_hud_status.configure(text="● HBSP TURBO READY", text_color=CLR_ACCENT_CYAN)
            self.lbl_hud_speed.configure(text="Speed: 0.0 MB/s • ETA: --", text_color=CLR_TEXT_MUTED)

    def recv_exact(self, sock, num_bytes):
        data = bytearray()
        while len(data) < num_bytes:
            packet = sock.recv(num_bytes - len(data))
            if not packet: raise EOFError("Connection closed by peer")
            data.extend(packet)
        return bytes(data)

    def send_file_hbsp(self):
        """True 1-Click Fast Send: If no file is chosen, prompts immediately and starts gigabit transfer!"""
        ip = self.ip_entry.get().strip()
        if not ip or ip.endswith("."):
            self.log("[FILE ERROR] Phone IP is incomplete. Please enter a valid Phone IP or click 🔍 Auto.")
            self.update_transfer_ui(0.0, "Error: Android Phone IP is empty!", 0.0, mode="error")
            return

        file_path = getattr(self, '_current_file_path', None)
        if not file_path or not os.path.exists(file_path):
            file_path = filedialog.askopenfilename()
            if not file_path:
                return
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            name = os.path.basename(file_path)
            self.selected_file_path.set(f"{name} ({size_mb:.1f} MB)")
            self._current_file_path = file_path
            self.lbl_selected_file.configure(
                text=f"✓ Selected: {name} ({size_mb:.1f} MB) • Sending...",
                text_color=CLR_ACCENT_CYAN
            )
            self.log(f"[FILE] 1-Click Selected & Sending: {name} ({size_mb:.1f} MB)")

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
            self.after(0, lambda: self.update_transfer_ui(0.01, f"Connecting to {ip}:8082...", 0.0, mode="sending"))

            master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            master_sock.settimeout(12.0)
            master_sock.connect((ip, 8082))

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
                    if diff >= 0.18:
                        speed = (bytes_since_calc / (1024 * 1024)) / diff
                        pct = uploaded / file_size if file_size > 0 else 0
                        up_mb = uploaded / (1024 * 1024)
                        rem_mb = max(0, total_mb - up_mb)
                        eta = int(rem_mb / speed) if speed > 0.1 else 0
                        status_str = f"{up_mb:.1f}/{total_mb:.1f} MB (ETA {eta}s)"
                        self.after(0, lambda p=pct, s=status_str, sp=speed, e=eta: self.update_transfer_ui(p, s, sp, mode="sending", eta=e))
                        last_calc_time = now
                        bytes_since_calc = 0

            master_sock.sendall(bytes([OP_DIRECT_COMPLETE]))
            comp_ack = self.recv_exact(master_sock, 7)

            total_time = max(0.1, time.time() - start_time)
            avg_speed = (file_size / (1024 * 1024)) / total_time
            self.log(f"[FILE SUCCESS] ✓ Sent {file_name} ({total_mb:.1f} MB) in {total_time:.1f}s (Avg: {avg_speed:.1f} MB/s)!")
            self.after(0, lambda: self.update_transfer_ui(1.0, f"Complete in {total_time:.1f}s!", avg_speed, mode="success"))
            self.after(0, self.refresh_file_lists)
            self._current_file_path = None
        except Exception as e:
            self.log(f"[TRANSFER ERROR] {e}")
            self.after(0, lambda: self.update_transfer_ui(0.0, f"Error: {e}", 0.0, mode="error"))
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
                        if diff >= 0.18:
                            speed = (bytes_since_calc / (1024 * 1024)) / diff
                            pct = received / file_size if file_size > 0 else 0
                            rec_mb = received / (1024 * 1024)
                            rem_mb = max(0, total_mb - rec_mb)
                            eta = int(rem_mb / speed) if speed > 0.1 else 0
                            status_str = f"📥 {rec_mb:.1f}/{total_mb:.1f} MB"
                            self.after(0, lambda p=pct, s=status_str, sp=speed, e=eta: self.update_transfer_ui(p, s, sp, mode="receiving", eta=e))
                            last_calc_time = now
                            bytes_since_calc = 0

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
                self.after(0, lambda: self.update_transfer_ui(1.0, f"Complete in {total_time:.1f}s!", avg_speed, mode="success"))
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
                channel_idx = sock.recv(1)[0]
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
        """Probes both File port (8082) and Audio port (8080) for active P2Pwire Android service."""
        for port in (8082, 8080):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.25)
                res = sock.connect_ex((ip, port))
                sock.close()
                if res == 0:
                    return ip, port
            except:
                pass
        return None

    def start_auto_detect_thread(self):
        if self.is_scanning: return
        threading.Thread(target=self.detect_android_device, args=(False,), daemon=True).start()

    def detect_android_device(self, silent_startup=False):
        self.is_scanning = True
        if not silent_startup:
            self.log("[SCAN] Auto-detecting Android device on local network (Ports 8080 & 8082)...")
            self.after(0, lambda: self.status_pill.configure(text="● SCANNING...", text_color=CLR_ACCENT_CYAN))

        found = None
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

        for gw in ["192.168.43.1", "192.168.44.1", "192.168.50.1", "192.168.1.1", "192.168.0.1"]:
            if gw not in candidates:
                candidates.append(gw)

        for cand in candidates:
            res = self.probe_ip(cand)
            if res:
                found = res
                break

        if not found:
            try:
                local_ip = self.get_pc_local_ip()
                if not local_ip.startswith("127."):
                    prefix = ".".join(local_ip.split(".")[:3])
                    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                        futures = [executor.submit(self.probe_ip, f"{prefix}.{i}") for i in range(1, 255)]
                        for f in concurrent.futures.as_completed(futures):
                            r = f.result()
                            if r:
                                found = r
                                break
            except Exception:
                pass

        self.is_scanning = False
        if found:
            found_ip, found_port = found
            self.log(f"[AUTO-FOUND] ✓ Found Android Device at {found_ip} on port {found_port}!")
            self.after(0, lambda: self.ip_entry.delete(0, "end"))
            self.after(0, lambda: self.ip_entry.insert(0, found_ip))
            self.after(0, lambda: self.status_pill.configure(text=f"● FOUND ({found_ip})", text_color=CLR_ACCENT_GREEN))
        else:
            if not silent_startup:
                self.log("[SCAN] No active Android device responded. Look at your Phone's screen for 'Phone IP' and enter it manually.")
                self.after(0, lambda: self.status_pill.configure(text="● STANDBY", text_color=CLR_ACCENT_AMBER))


if __name__ == "__main__":
    app = P2PwireApp()
    app.mainloop()
