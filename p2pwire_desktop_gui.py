# ==============================================================================
# P2Pwire Premium Desktop GUI Client (Cyberpunk Edition)
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

CHUNK_SIZE = 128 * 1024
DEFAULT_CHANNELS = 4

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
        self.title("P2Pwire Turbo Studio • Wireless Audio & High-Speed File Hub")
        self.geometry("640x840")
        self.minsize(580, 780)
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
        self.waveform_levels = [0.0] * 44
        self.waveform_peaks = [0.0] * 44
        self.laser_pos = 0.0

        self.setup_header()
        self.setup_tabs()
        self.setup_connection_bar()

        self.animate_loop()
        threading.Thread(target=self.detect_android_device, args=(True,), daemon=True).start()
        threading.Thread(target=self.run_pc_hbsp_receiver, daemon=True).start()

    def setup_header(self):
        header_card = ctk.CTkFrame(self, fg_color=CLR_CARD, corner_radius=14, border_width=1, border_color=CLR_CARD_BORDER)
        header_card.pack(fill="x", padx=16, pady=(12, 6))

        top_row = ctk.CTkFrame(header_card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(10, 2))

        title_box = ctk.CTkFrame(top_row, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="⚡ P2Pwire Turbo Hub",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CLR_TEXT_BRIGHT
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text="Real-Time Audio Stream & High-Speed File Transfer",
            font=ctk.CTkFont(size=11),
            text_color=CLR_TEXT_MUTED
        ).pack(anchor="w")

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
        self.tab_files = self.tabview.add("⚡ File Sharing Hub")

        self.setup_audio_tab_content()
        self.setup_files_tab_content()

    def setup_audio_tab_content(self):
        tab = self.tab_audio

        info_banner = ctk.CTkFrame(tab, fg_color="#0B132B", corner_radius=10, border_width=1, border_color="#1E293B")
        info_banner.pack(fill="x", padx=12, pady=(8, 6))

        ctk.CTkLabel(
            info_banner,
            text="🎵 Pure 48,000 Hz 16-Bit PCM • Hardware Loopback Audio",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CLR_ACCENT_CYAN
        ).pack(pady=6)

        self.canvas_visualizer = tk.Canvas(
            tab,
            width=580,
            height=140,
            bg="#0B0F19",
            highlightthickness=1,
            highlightbackground="#1E293B"
        )
        self.canvas_visualizer.pack(fill="x", padx=12, pady=6)

        self.bar_items = []
        self.peak_items = []
        bar_count = 44
        bar_w = 9
        gap = 4
        start_x = 10
        for i in range(bar_count):
            x1 = start_x + i * (bar_w + gap)
            x2 = x1 + bar_w
            b = self.canvas_visualizer.create_rectangle(x1, 70, x2, 70, fill="#1E293B", outline="")
            p = self.canvas_visualizer.create_line(x1, 70, x2, 70, fill="#38BDF8", width=2)
            self.bar_items.append(b)
            self.peak_items.append(p)

        self.canvas_hint = self.canvas_visualizer.create_text(
            290, 70,
            text="✦ Audio Engine Idle • Ready to Stream",
            fill="#64748B",
            font=("Segoe UI", 11, "italic")
        )

        self.btn_toggle_stream = ctk.CTkButton(
            tab,
            text="▶ Start Streaming PC Audio to Phone Speaker",
            command=self.toggle_stream,
            height=46,
            corner_radius=10,
            fg_color=CLR_ACCENT_CYAN,
            hover_color="#0891B2",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_toggle_stream.pack(fill="x", padx=12, pady=(8, 6))

    def setup_files_tab_content(self):
        tab = self.tab_files

        top_action_row = ctk.CTkFrame(tab, fg_color="transparent")
        top_action_row.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkButton(
            top_action_row,
            text="📁 Select File from PC",
            command=self.select_file,
            height=38,
            corner_radius=8,
            fg_color="#1E293B",
            hover_color="#334155",
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            top_action_row,
            text="🚀 Send File",
            command=self.send_file_hbsp,
            height=38,
            corner_radius=8,
            fg_color=CLR_ACCENT_GREEN,
            hover_color="#059669",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=110
        ).pack(side="right")

        self.canvas_progress = tk.Canvas(
            tab,
            width=580,
            height=36,
            bg="#0B0F19",
            highlightthickness=1,
            highlightbackground="#1E293B"
        )
        self.canvas_progress.pack(fill="x", padx=12, pady=4)
        self.progress_rect = self.canvas_progress.create_rectangle(0, 0, 0, 36, fill="#047857", outline="")
        self.laser_rect = self.canvas_progress.create_rectangle(0, 0, 0, 36, fill="#34D399", outline="", state="hidden")
        self.progress_text = self.canvas_progress.create_text(
            290, 18,
            text="Ready for Transfer",
            fill="#E2E8F0",
            font=("Segoe UI", 11, "bold")
        )

        self.file_subtabview = ctk.CTkTabview(
            tab,
            fg_color="#0F172A",
            segmented_button_fg_color="#090D16",
            segmented_button_selected_color=CLR_ACCENT_GREEN,
            segmented_button_selected_hover_color="#059669",
            segmented_button_unselected_color="#1E293B",
            segmented_button_unselected_hover_color="#334155",
            corner_radius=10,
            border_width=1,
            border_color="#1E293B"
        )
        self.file_subtabview.pack(fill="both", expand=True, padx=12, pady=4)

        self.subtab_received = self.file_subtabview.add("📥 Received")
        self.subtab_sent = self.file_subtabview.add("📤 Sent")

        self.setup_received_list(self.subtab_received)
        self.setup_sent_list(self.subtab_sent)
        self.refresh_file_lists()

    def setup_received_list(self, subtab):
        header_row = ctk.CTkFrame(subtab, fg_color="transparent")
        header_row.pack(fill="x", padx=6, pady=(4, 2))

        ctk.CTkLabel(header_row, text="Received Files", font=ctk.CTkFont(size=12, weight="bold"), text_color=CLR_TEXT_MUTED).pack(side="left")
        ctk.CTkButton(header_row, text="Open Folder", command=lambda: self.open_folder(self.received_folder), width=90, height=24, fg_color="#1E293B", font=ctk.CTkFont(size=10)).pack(side="right")

        self.received_scroll = ctk.CTkScrollableFrame(subtab, fg_color="transparent", height=240)
        self.received_scroll.pack(fill="both", expand=True, padx=2, pady=2)

    def setup_sent_list(self, subtab):
        header_row = ctk.CTkFrame(subtab, fg_color="transparent")
        header_row.pack(fill="x", padx=6, pady=(4, 2))

        ctk.CTkLabel(header_row, text="Sent Files", font=ctk.CTkFont(size=12, weight="bold"), text_color=CLR_TEXT_MUTED).pack(side="left")
        ctk.CTkButton(header_row, text="Open Folder", command=lambda: self.open_folder(self.sent_folder), width=90, height=24, fg_color="#1E293B", font=ctk.CTkFont(size=10)).pack(side="right")

        self.sent_scroll = ctk.CTkScrollableFrame(subtab, fg_color="transparent", height=240)
        self.sent_scroll.pack(fill="both", expand=True, padx=2, pady=2)

    def refresh_file_lists(self):
        if 'received_scroll' in self.__dict__:
            for widget in self.received_scroll.winfo_children():
                widget.destroy()

            try:
                files = sorted([os.path.join(self.received_folder, f) for f in os.listdir(self.received_folder)], key=os.path.getmtime, reverse=True)
                for fpath in files:
                    if os.path.isfile(fpath):
                        self.add_file_card(self.received_scroll, fpath, is_received=True)
            except Exception:
                pass

        if 'sent_scroll' in self.__dict__:
            for widget in self.sent_scroll.winfo_children():
                widget.destroy()

            try:
                files = sorted([os.path.join(self.sent_folder, f) for f in os.listdir(self.sent_folder)], key=os.path.getmtime, reverse=True)
                for fpath in files:
                    if os.path.isfile(fpath):
                        self.add_file_card(self.sent_scroll, fpath, is_received=False)
            except Exception:
                pass

    def add_file_card(self, parent, fpath, is_received):
        fname = os.path.basename(fpath)
        fsize = os.path.getsize(fpath) / (1024 * 1024)

        card = ctk.CTkFrame(parent, fg_color="#1E293B", corner_radius=8)
        card.pack(fill="x", padx=4, pady=3)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=8)

        info_box = ctk.CTkFrame(row, fg_color="transparent")
        info_box.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(info_box, text=fname, font=ctk.CTkFont(size=12, weight="bold"), text_color=CLR_TEXT_BRIGHT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(info_box, text=f"{fsize:.1f} MB", font=ctk.CTkFont(size=10), text_color=CLR_TEXT_MUTED, anchor="w").pack(anchor="w")

        ctk.CTkButton(
            row,
            text="▶ Play/Open",
            command=lambda: self.open_file_path(fpath),
            width=85,
            height=28,
            corner_radius=6,
            fg_color=CLR_ACCENT_GREEN,
            hover_color="#059669",
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            row,
            text="📁 Folder",
            command=lambda: self.open_folder(os.path.dirname(fpath)),
            width=65,
            height=28,
            corner_radius=6,
            fg_color="#334155",
            hover_color="#475569",
            font=ctk.CTkFont(size=11)
        ).pack(side="right")

    def open_file_path(self, fpath):
        try:
            if platform.system() == "Windows":
                os.startfile(fpath)
            elif platform.system() == "Darwin":
                subprocess.run(["open", fpath])
            else:
                subprocess.run(["xdg-open", fpath])
        except Exception:
            pass

    def open_folder(self, dpath):
        if os.path.exists(dpath):
            try:
                if platform.system() == "Windows":
                    os.startfile(dpath)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", dpath])
                else:
                    subprocess.Popen(["xdg-open", dpath])
            except Exception:
                pass

    def setup_connection_bar(self):
        conn_frame = ctk.CTkFrame(self, fg_color=CLR_CARD, corner_radius=14, border_width=1, border_color=CLR_CARD_BORDER)
        conn_frame.pack(fill="x", padx=16, pady=4)

        input_row = ctk.CTkFrame(conn_frame, fg_color="transparent")
        input_row.pack(fill="x", padx=14, pady=10)

        self.ip_entry = ctk.CTkEntry(
            input_row,
            placeholder_text="Android IP (e.g. 192.168.43.1)",
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

    def animate_loop(self):
        self.anim_tick += 1
        if not self.is_streaming:
            self.canvas_visualizer.itemconfig(self.canvas_hint, state="normal")
            for i in range(len(self.bar_items)):
                wave = math.sin(self.anim_tick * 0.08 + i * 0.22) * 12 + 14
                self.canvas_visualizer.coords(self.bar_items[i], 10 + i * 13, 70 - wave, 10 + i * 13 + 9, 70 + wave)
                self.canvas_visualizer.itemconfig(self.bar_items[i], fill="#1E293B")
                self.canvas_visualizer.coords(self.peak_items[i], 10 + i * 13, 70 - wave - 2, 10 + i * 13 + 9, 70 - wave - 2)
                self.canvas_visualizer.itemconfig(self.peak_items[i], fill="#334155")
        else:
            self.canvas_visualizer.itemconfig(self.canvas_hint, state="hidden")
            for i in range(len(self.bar_items)):
                cur = self.waveform_levels[i]
                peak = self.waveform_peaks[i]
                peak = max(cur, peak - 1.2)
                self.waveform_peaks[i] = peak
                self.waveform_levels[i] = max(2.0, cur * 0.88)

                bar_color = CLR_ACCENT_CYAN if cur < 30 else CLR_ACCENT_GREEN
                x1 = 10 + i * 13
                x2 = x1 + 9
                self.canvas_visualizer.coords(self.bar_items[i], x1, 70 - cur, x2, 70 + cur)
                self.canvas_visualizer.itemconfig(self.bar_items[i], fill=bar_color)
                self.canvas_visualizer.coords(self.peak_items[i], x1, 70 - peak - 2, x2, 70 - peak - 2)
                self.canvas_visualizer.itemconfig(self.peak_items[i], fill="#38BDF8")

        if self.is_transferring:
            self.laser_pos = (self.laser_pos + 16) % 580
            self.canvas_progress.itemconfig(self.laser_rect, state="normal")
            self.canvas_progress.coords(self.laser_rect, self.laser_pos, 0, self.laser_pos + 30, 36)
        else:
            self.canvas_progress.itemconfig(self.laser_rect, state="hidden")

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

    def toggle_stream(self):
        if not self.is_streaming:
            ip = self.ip_entry.get().strip()
            if not ip: return

            with self.stream_lock:
                self.is_streaming = True
                self.stop_stream_event.clear()
                self.btn_toggle_stream.configure(text="⏹ Stop Streaming Audio", fg_color="#EF4444", hover_color="#DC2626")
                self.status_pill.configure(text="● STREAMING LIVE", text_color=CLR_ACCENT_GREEN)
                self.stream_thread = threading.Thread(target=self.run_audio_worker, args=(ip,), daemon=True)
                self.stream_thread.start()
        else:
            self.stop_streaming_cleanly()

    def stop_streaming_cleanly(self):
        with self.stream_lock:
            self.is_streaming = False
            self.stop_stream_event.set()
            self.btn_toggle_stream.configure(text="▶ Start Streaming PC Audio to Phone Speaker", fg_color=CLR_ACCENT_CYAN, hover_color="#0891B2")
            self.status_pill.configure(text="● STANDBY", text_color=CLR_ACCENT_AMBER)

    def run_audio_worker(self, ip):
        async def stream():
            uri = f"ws://{ip}:8080/ws/upload_audio"
            while self.is_streaming and not self.stop_stream_event.is_set():
                ws = None
                try:
                    ws = await websockets.connect(uri, ping_interval=20, ping_timeout=15, max_size=None)
                    if not HAS_SOUNDCARD: break
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
                        except: pass

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
                except:
                    pass
                finally:
                    if ws:
                        try: await ws.close()
                        except: pass
                if not self.is_streaming or self.stop_stream_event.is_set(): break
                await asyncio.sleep(2)
            self.after(0, self.stop_streaming_cleanly)

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

    def update_transfer_ui(self, progress, status_text, speed_mbps=0.0):
        bar_width = int(progress * 580)
        self.canvas_progress.coords(self.progress_rect, 0, 0, bar_width, 36)
        pct_label = f"⚡ {int(progress * 100)}% • {status_text} • {speed_mbps:.1f} MB/s"
        self.canvas_progress.itemconfig(self.progress_text, text=pct_label)
        self.transfer_progress.set(status_text)

    def recv_exact(self, sock, num_bytes):
        data = bytearray()
        while len(data) < num_bytes:
            packet = sock.recv(num_bytes - len(data))
            if not packet: raise EOFError("Connection closed")
            data.extend(packet)
        return bytes(data)

    def send_file_hbsp(self):
        ip = self.ip_entry.get().strip()
        file_path = getattr(self, '_current_file_path', None)
        if not ip or not file_path or not os.path.exists(file_path): return
        threading.Thread(target=self.perform_hbsp_turbo_upload, args=(ip, file_path), daemon=True).start()

    def perform_hbsp_turbo_upload(self, ip, file_path):
        self.is_transferring = True
        master_sock = None
        try:
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)
            name_bytes = file_name.encode('utf-8')

            try:
                dest_sent = os.path.join(self.sent_folder, file_name)
                with open(file_path, 'rb') as src, open(dest_sent, 'wb') as dst:
                    dst.write(src.read())
                self.after(0, self.refresh_file_lists)
            except: pass

            num_channels = DEFAULT_CHANNELS
            session_id = random.randint(100000, 999999)

            master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            master_sock.connect((ip, 8082))

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

            ack_data = self.recv_exact(master_sock, 11)
            if not ack_data.startswith(HBSP_MAGIC) or ack_data[5] != OP_TURBO_INIT_ACK:
                raise Exception("Handshake rejected")

            part_size = file_size // num_channels
            thread_sent = [0] * num_channels

            def worker_channel(ch_idx, start_offset, length):
                wsock = None
                try:
                    wsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    wsock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    wsock.connect((ip, 8082))

                    jbuf = bytearray()
                    jbuf.extend(HBSP_MAGIC)
                    jbuf.append(HBSP_VERSION)
                    jbuf.append(OP_TURBO_CHANNEL_JOIN)
                    jbuf.extend(struct.pack('>I', session_id))
                    jbuf.append(ch_idx)
                    jbuf.extend(struct.pack('>Q', start_offset))
                    jbuf.extend(struct.pack('>Q', length))
                    wsock.sendall(jbuf)

                    self.recv_exact(wsock, 7)

                    buf = bytearray(CHUNK_SIZE)
                    mv = memoryview(buf)
                    sent = 0
                    with open(file_path, 'rb') as f:
                        f.seek(start_offset)
                        while sent < length:
                            to_read = min(CHUNK_SIZE, length - sent)
                            n = f.readinto(mv[:to_read])
                            if not n: break
                            wsock.sendall(mv[:n])
                            sent += n
                            thread_sent[ch_idx] = sent
                except: pass
                finally:
                    if wsock:
                        try: wsock.close()
                        except: pass

            start_time = time.time()
            threads = []
            for ch in range(num_channels):
                start_off = ch * part_size
                ch_len = (file_size - start_off) if ch == (num_channels - 1) else part_size
                t = threading.Thread(target=worker_channel, args=(ch, start_off, ch_len), daemon=True)
                threads.append(t)
                t.start()

            while any(t.is_alive() for t in threads):
                time.sleep(0.15)
                cur_up = sum(thread_sent)
                pct = cur_up / file_size if file_size > 0 else 0
                elapsed = max(0.1, time.time() - start_time)
                speed = (cur_up / (1024 * 1024)) / elapsed
                self.after(0, lambda p=pct, s="Transferring...", sp=speed: self.update_transfer_ui(p, s, sp))

            for t in threads: t.join(timeout=2.0)

            master_sock.sendall(bytes([OP_TURBO_COMPLETE]))
            self.recv_exact(master_sock, 7)
            total_time = max(0.1, time.time() - start_time)
            avg_speed = (file_size / (1024 * 1024)) / total_time
            self.after(0, lambda: self.update_transfer_ui(1.0, "Complete!", avg_speed))
            self.after(0, self.refresh_file_lists)
        except Exception as e:
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
            while True:
                conn, addr = srv.accept()
                threading.Thread(target=self.handle_incoming_connection_on_pc, args=(conn, addr), daemon=True).start()
        except:
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
        except:
            pass
        finally:
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
        found_ip = None
        for gw in ["192.168.43.1", "192.168.44.1", "192.168.50.1", "192.168.1.1"]:
            if self.probe_ip(gw):
                found_ip = gw
                break
        self.is_scanning = False
        if found_ip:
            self.after(0, lambda: self.ip_entry.delete(0, "end"))
            self.after(0, lambda: self.ip_entry.insert(0, found_ip))
            self.after(0, lambda: self.status_pill.configure(text=f"● CONNECTED ({found_ip})", text_color=CLR_ACCENT_GREEN))


if __name__ == "__main__":
    app = P2PwireApp()
    app.mainloop()
