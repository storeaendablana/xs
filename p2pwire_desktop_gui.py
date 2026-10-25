#!/usr/bin/env python3
"""
================================================================================
  P2Pwire Desktop Turbo Hub 3.0 (Zero-Config Auto-Discovery & Turbo Engine)
  Bidirectional Sound & 1 GBps File Engine: PC ⇄ Phone, Phone ⇄ PC
================================================================================
Requirements:
  pip install zstandard soundcard numpy
"""

import sys
import os
import time
import socket
import struct
import zlib
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Facebook Zstandard compression
try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

# Sound loopback capture & playback (Fix for Windows Error 0x80070490)
try:
    import soundcard as sc
    import numpy as np
    HAS_SOUNDCARD = True
except ImportError:
    HAS_SOUNDCARD = False

# ==============================================================================
# HBSP Constants & Tuning (1 GBps Pipeline)
# ==============================================================================
HBSP_MAGIC = b'HBSP'
HBSP_VERSION = 3
FLAG_NONE = 0x00
FLAG_ZSTD = 0x01
FLAG_FILE = 0x08
FLAG_EOF = 0x20
FLAG_META = 0x40

CHUNK_SIZE = 512 * 1024  # 512 KB optimal L3 cache residency & TCP window
SOCKET_BUF_SIZE = 4 * 1024 * 1024  # 4 MB socket buffer
DISCOVERY_PORT = 8089
AUDIO_PORT = 8080
HBSP_FILE_PORT = 8082

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "P2Pwire")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


def get_local_ip():
    """Finds the local network IP of this PC."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ==============================================================================
# Zero-Config Auto-Discovery Engine (UDP Mesh Port 8089)
# ==============================================================================
class PeerAutoDiscovery:
    def __init__(self, local_ip, on_phone_found_callback):
        self.local_ip = local_ip
        self.on_found = on_phone_found_callback
        self.running = True
        self.found_ip = None

    def start(self):
        threading.Thread(target=self._listen_udp_beacon, daemon=True).start()
        threading.Thread(target=self._broadcast_pc_beacon, daemon=True).start()
        threading.Thread(target=self._fast_subnet_sweep, daemon=True).start()

    def _listen_udp_beacon(self):
        """Listens for the Phone's UDP beacon on port 8089."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", DISCOVERY_PORT))
            s.settimeout(2.0)
            while self.running:
                try:
                    data, addr = s.recvfrom(512)
                    msg = data.decode("utf-8", errors="ignore")
                    if msg.startswith("P2PWIRE_BEACON:ANDROID:"):
                        phone_ip = msg.split(":")[2]
                        self._report_found(phone_ip)
                except socket.timeout:
                    continue
            s.close()
        except Exception:
            pass

    def _broadcast_pc_beacon(self):
        """Broadcasts PC presence every 1.5 seconds."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            msg = f"P2PWIRE_BEACON:PC:{self.local_ip}".encode("utf-8")
            while self.running:
                try:
                    s.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
                except Exception:
                    pass
                time.sleep(1.5)
            s.close()
        except Exception:
            pass

    def _fast_subnet_sweep(self):
        """Sweeps known subnet candidates (e.g. 10.119.76.208) in parallel."""
        parts = self.local_ip.split(".")
        if len(parts) != 4:
            return
        base = f"{parts[0]}.{parts[1]}.{parts[2]}."

        candidates = [208, 100, 101, 102, 105, 150, 168, 2, 3, 4, 5] + list(range(10, 60))
        for last in candidates:
            if self.found_ip or not self.running:
                break
            ip = f"{base}{last}"
            if ip == self.local_ip:
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.15)
                s.connect((ip, AUDIO_PORT))
                s.close()
                self._report_found(ip)
                break
            except Exception:
                continue

    def _report_found(self, ip):
        self.found_ip = ip
        if self.on_found:
            self.on_found(ip)

    def stop(self):
        self.running = False


# ==============================================================================
# Zero-Effort Sound Streamer (PC ➡️ Phone Speaker, Port 8080)
# ==============================================================================
class DirectAudioStreamer:
    def __init__(self, target_ip, port=AUDIO_PORT, log_callback=None):
        self.target_ip = target_ip
        self.port = port
        self.log = log_callback or print
        self.running = False
        self.thread = None
        self.current_volume = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _stream_worker(self):
        if not HAS_SOUNDCARD:
            self.log("[ERROR] 'soundcard' or 'numpy' not installed! Run: pip install soundcard numpy", "red")
            self.running = False
            return

        self.log(f"[SPEAKER] Connecting to Phone Speaker at {self.target_ip}:{self.port}...", "cyan")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(3.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(None)

            sock.sendall(b"STREAM_PCM_48000_16BIT\r\n\r\n")
            self.log(f"[SPEAKER ACTIVE] ✓ PC Sound streaming to Phone Speaker ({self.target_ip})!", "green")

            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)

            with mic.recorder(samplerate=48000, channels=2, blocksize=1024) as rec:
                while self.running:
                    data = rec.record(numframes=1024)
                    peak = np.max(np.abs(data)) if len(data) > 0 else 0.0
                    self.current_volume = float(min(1.0, peak * 1.5))
                    pcm16 = (data * 32767.0).clip(-32768, 32767).astype(np.int16).tobytes()
                    sock.sendall(pcm16)

            sock.close()
        except Exception as e:
            if self.running:
                self.log(f"[SPEAKER ERROR] Connection error: {e}", "red")
        finally:
            self.running = False
            self.log("[SPEAKER] Sound stream stopped.", "white")


# ==============================================================================
# Background Audio Receiver Server (Phone ➡️ PC Speakers, Port 8080)
# ==============================================================================
class AudioReceiverServer:
    def __init__(self, port=AUDIO_PORT, log_callback=None):
        self.port = port
        self.log = log_callback or print
        self.running = True

    def start(self):
        threading.Thread(target=self._server_worker, daemon=True).start()

    def _server_worker(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.port))
            server.listen(5)
            self.log(f"[AUDIO SERVER] Listening on port {self.port} (Ready to play Phone sound on PC speakers)", "white")

            while self.running:
                client, addr = server.accept()
                threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()
        except Exception as e:
            if self.running:
                self.log(f"[AUDIO SERVER] Port {self.port} in use or notice: {e}", "yellow")

    def _handle_client(self, client, addr):
        self.log(f"[PHONE AUDIO] Connected from Phone ({addr[0]})! Playing sound on PC...", "green")
        try:
            client.settimeout(None)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            if HAS_SOUNDCARD:
                speaker = sc.default_speaker()
                with speaker.player(samplerate=48000, channels=2, blocksize=1024) as player:
                    while self.running:
                        data = client.recv(4096)
                        if not data:
                            break
                        if data.startswith(b"STREAM_PCM") or data.startswith(b"GET /"):
                            continue
                        count = len(data) - (len(data) % 4)
                        if count > 0:
                            pcm16 = np.frombuffer(data[:count], dtype=np.int16)
                            float_audio = pcm16.astype(np.float32) / 32768.0
                            if len(float_audio) % 2 == 0:
                                stereo = float_audio.reshape(-1, 2)
                                player.play(stereo)
            else:
                while self.running:
                    data = client.recv(4096)
                    if not data:
                        break
        except Exception as e:
            pass
        finally:
            client.close()
            self.log(f"[PHONE AUDIO] Disconnected ({addr[0]}).", "white")


# ==============================================================================
# Background HBSP File Receiver Server (Phone ➡️ PC Files, Port 8082)
# ==============================================================================
class HbspFileReceiverServer:
    def __init__(self, port=HBSP_FILE_PORT, log_callback=None, on_file_received_callback=None):
        self.port = port
        self.log = log_callback or print
        self.on_file_received = on_file_received_callback
        self.running = True

    def start(self):
        threading.Thread(target=self._server_worker, daemon=True).start()

    def _server_worker(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
            server.bind(("0.0.0.0", self.port))
            server.listen(5)
            self.log(f"[HBSP FILE SERVER] Listening on port {self.port} (Files auto-save to ~/Downloads/P2Pwire)", "white")

            dctx = zstd.ZstdDecompressor() if HAS_ZSTD else None

            while self.running:
                client, addr = server.accept()
                threading.Thread(target=self._handle_file_client, args=(client, addr, dctx), daemon=True).start()
        except Exception as e:
            if self.running:
                self.log(f"[HBSP SERVER ERROR] Port {self.port} error: {e}", "yellow")

    def _handle_file_client(self, client, addr, dctx):
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
        self.log(f"[INCOMING FILE] Receiving file blast from Phone ({addr[0]})...", "cyan")

        current_filename = f"P2Pwire_{int(time.time())}.bin"
        target_path = os.path.join(DOWNLOADS_DIR, current_filename)
        file_obj = None
        start_time = time.time()
        total_bytes = 0

        try:
            while self.running:
                # Read 24-byte HBSP header: !4sBBHIIII
                header_data = self._read_exact(client, 24)
                if not header_data:
                    break

                magic, ver, flags, reserved, seq, payload_len, decomp_len, checksum = struct.unpack("!4sBBHIIII", header_data)
                if magic != HBSP_MAGIC:
                    break

                payload = self._read_exact(client, payload_len)
                if not payload:
                    break

                # Decompress if Zstd flag is set
                is_zstd = (flags & FLAG_ZSTD) != 0
                if is_zstd and dctx:
                    try:
                        decompressed = dctx.decompress(payload, max_output_size=decomp_len)
                    except Exception:
                        decompressed = payload
                else:
                    decompressed = payload

                # Check if this frame is filename metadata
                is_meta = (flags & FLAG_META) != 0
                if is_meta:
                    name_str = decompressed.decode("utf-8", errors="ignore").strip()
                    if name_str:
                        clean_name = os.path.basename(name_str)
                        current_filename = clean_name
                        target_path = os.path.join(DOWNLOADS_DIR, clean_name)
                        self.log(f"[FILE NAME] Original filename detected: '{clean_name}'", "cyan")

                # If file transfer payload
                if (flags & FLAG_FILE) and not is_meta:
                    if file_obj is None:
                        file_obj = open(target_path, "wb")
                    file_obj.write(decompressed)
                    total_bytes += len(decompressed)

                # EOF frame
                if flags & FLAG_EOF:
                    if file_obj:
                        file_obj.flush()
                        file_obj.close()
                        file_obj = None
                    elapsed = max(0.001, time.time() - start_time)
                    mbps = (total_bytes / (1024 * 1024)) / elapsed
                    gbps = (mbps * 8) / 1000
                    self.log(f"[FILE SUCCESS] ✓ '{current_filename}' ({total_bytes / (1024*1024):.2f} MB) saved! Speed: {mbps:.1f} MB/s ({gbps:.2f} Gbps)", "green")
                    if self.on_file_received:
                        self.on_file_received(current_filename, target_path)
                    break
        except Exception as e:
            self.log(f"[FILE RECV ERROR] {e}", "red")
        finally:
            if file_obj:
                try: file_obj.close()
                except Exception: pass
            client.close()

    def _read_exact(self, sock, num_bytes):
        buf = bytearray()
        while len(buf) < num_bytes:
            chunk = sock.recv(num_bytes - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)


# ==============================================================================
# Single-Action File Sender (PC ➡️ Phone, Port 8082, 1 GBps Pipeline)
# ==============================================================================
class TurboFileSender:
    def __init__(self, target_ip, port=HBSP_FILE_PORT, log_callback=None, progress_callback=None):
        self.target_ip = target_ip
        self.port = port
        self.log = log_callback or print
        self.progress = progress_callback
        self.cancelled = False

    def send_file(self, file_path):
        threading.Thread(target=self._send_file_worker, args=(file_path,), daemon=True).start()

    def cancel(self):
        self.cancelled = True

    def _send_file_worker(self, file_path):
        self.cancelled = False
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        self.log(f"[TURBO 3.0] Blasting '{file_name}' ({file_size / (1024*1024):.2f} MB) to Phone with Zstd...", "cyan")

        cctx = zstd.ZstdCompressor(level=1) if HAS_ZSTD else None

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF_SIZE)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
            sock.settimeout(4.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(None)

            # Step 1: Send original filename in metadata frame
            meta_payload = file_name.encode("utf-8")
            checksum = zlib.adler32(meta_payload) & 0xFFFFFFFF
            meta_header = struct.pack("!4sBBHIIII", HBSP_MAGIC, HBSP_VERSION, FLAG_FILE | FLAG_META, 0, 0, len(meta_payload), len(meta_payload), checksum)
            sock.sendall(meta_header + meta_payload)

            # Step 2: Stream file chunks with Zstandard Turbo
            start_time = time.time()
            total_sent_raw = 0
            total_sent_wire = len(meta_header) + len(meta_payload)
            seq = 1

            with open(file_path, "rb") as f:
                while total_sent_raw < file_size and not self.cancelled:
                    raw_chunk = f.read(CHUNK_SIZE)
                    if not raw_chunk:
                        break

                    is_eof = (total_sent_raw + len(raw_chunk) >= file_size)
                    payload = raw_chunk
                    flags = FLAG_FILE
                    decompressed_len = len(raw_chunk)

                    if HAS_ZSTD and cctx is not None:
                        compressed = cctx.compress(raw_chunk)
                        if len(compressed) < len(raw_chunk):
                            payload = compressed
                            flags |= FLAG_ZSTD

                    if is_eof:
                        flags |= FLAG_EOF

                    checksum = zlib.adler32(payload) & 0xFFFFFFFF
                    header = struct.pack("!4sBBHIIII", HBSP_MAGIC, HBSP_VERSION, flags, 0, seq, len(payload), decompressed_len, checksum)

                    sock.sendall(header + payload)

                    total_sent_raw += len(raw_chunk)
                    total_sent_wire += len(header) + len(payload)
                    seq += 1

                    elapsed = max(0.001, time.time() - start_time)
                    speed_mbps = (total_sent_wire / (1024 * 1024)) / elapsed
                    gbps = (speed_mbps * 8) / 1000
                    ratio = total_sent_raw / max(1, total_sent_wire)

                    if self.progress:
                        self.progress(total_sent_raw, file_size, speed_mbps, gbps, ratio)

            sock.close()
            elapsed = max(0.001, time.time() - start_time)
            avg_speed = (total_sent_wire / (1024 * 1024)) / elapsed
            self.log(f"[TURBO SUCCESS] Sent '{file_name}' in {elapsed:.2f}s (Avg: {avg_speed:.1f} MB/s | {(avg_speed*8)/1000:.2f} Gbps)", "green")

        except Exception as e:
            self.log(f"[TURBO ERROR] Transfer failed: {e}", "red")


# ==============================================================================
# Ultra-Clean, Zero-Effort GUI (AirDrop / Quick Share Quality)
# ==============================================================================
class P2PwireDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("P2Pwire Turbo Hub 3.0")
        self.root.geometry("820x640")
        self.root.minsize(740, 560)
        self.root.configure(bg="#070B14")

        self.local_ip = get_local_ip()
        self.phone_ip = "10.119.76.208"  # Initial fallback until auto-discovered
        self.audio_streamer = None
        self.file_sender = None

        self._init_styles()
        self._build_ui()
        self._start_vu_meter_timer()

        # Start Zero-Config Auto-Discovery
        self.discovery = PeerAutoDiscovery(self.local_ip, self._on_phone_auto_discovered)
        self.discovery.start()

        # Start Background Servers for Phone ➡️ PC transfers
        self.file_server = HbspFileReceiverServer(port=HBSP_FILE_PORT, log_callback=self.log, on_file_received_callback=self._on_file_received_from_phone)
        self.file_server.start()

        self.audio_server = AudioReceiverServer(port=AUDIO_PORT, log_callback=self.log)
        self.audio_server.start()

        self.log("[INIT] P2Pwire Desktop Turbo Hub 3.0 Active.", "cyan")
        self.log(f"[PC IP] {self.local_ip} • Bidirectional Sound & 1 GBps File Engine Ready.", "white")
        if HAS_ZSTD:
            self.log("[ZSTD] ✓ Facebook Zstandard Turbo Engine active.", "green")
        else:
            self.log("💡 Tip: Run 'pip install zstandard' for maximum 1 GBps compression speed.", "yellow")

    def _init_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar", thickness=6, troughcolor="#101728", background="#00F0FF")

    def _build_ui(self):
        # Header Banner
        header = tk.Frame(self.root, bg="#101728", height=65)
        header.pack(fill=tk.X, padx=14, pady=10)

        title_box = tk.Frame(header, bg="#101728")
        title_box.pack(side=tk.LEFT, padx=16, pady=12)

        tk.Label(title_box, text="P2Pwire Turbo Hub 3.0", font=("Segoe UI", 16, "bold"), fg="#FFFFFF", bg="#101728").pack(anchor="w")
        self.status_subtext = tk.Label(title_box, text=f"Local PC: {self.local_ip} • Auto-scanning Wi-Fi...", font=("Consolas", 10), fg="#00F0FF", bg="#101728")
        self.status_subtext.pack(anchor="w")

        self.badge = tk.Label(header, text="MESH SCANNING", font=("Consolas", 10, "bold"), fg="#FFD600", bg="#070B14", padx=10, pady=4)
        self.badge.pack(side=tk.RIGHT, padx=16, pady=16)

        # Main Dual Action Cards
        cards_row = tk.Frame(self.root, bg="#070B14")
        cards_row.pack(fill=tk.X, padx=14, pady=8)

        # Card 1: Wireless Speaker Rescue (Port 8080)
        audio_card = tk.LabelFrame(cards_row, text=" 🔊 Universal Sound (Port 8080) ", font=("Segoe UI", 11, "bold"), fg="#7C4DFF", bg="#101728", padx=14, pady=12)
        audio_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 7))

        tk.Label(audio_card, text="Bidirectional sound: PC ⇄ Phone speaker rescue.", font=("Segoe UI", 9), fg="#94A3B8", bg="#101728").pack(anchor="w", pady=(0, 8))

        # Live VU Meter
        vu_box = tk.Frame(audio_card, bg="#101728")
        vu_box.pack(fill=tk.X, pady=6)
        tk.Label(vu_box, text="Sound VU:", font=("Consolas", 9), fg="#94A3B8", bg="#101728").pack(side=tk.LEFT)
        self.vu_bar = ttk.Progressbar(vu_box, length=160, mode="determinate")
        self.vu_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        self.btn_audio = tk.Button(audio_card, text="▶ Stream PC Sound to Phone", font=("Segoe UI", 10, "bold"), bg="#7C4DFF", fg="#FFFFFF", activebackground="#651FFF", relief=tk.FLAT, pady=10, command=self.toggle_audio_stream)
        self.btn_audio.pack(fill=tk.X, pady=(12, 0))

        # Card 2: Single-Action Turbo File Blast (Port 8082)
        file_card = tk.LabelFrame(cards_row, text=" ⚡ Superfast Turbo Drop (Port 8082) ", font=("Segoe UI", 11, "bold"), fg="#00FFA3", bg="#101728", padx=14, pady=12)
        file_card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(7, 0))

        tk.Label(file_card, text="Blast any movie or file to Phone with Zstd 1 GBps engine.", font=("Segoe UI", 9), fg="#94A3B8", bg="#101728").pack(anchor="w", pady=(0, 8))

        self.speed_label = tk.Label(file_card, text="Speed: 0.0 MB/s (0.00 Gbps)", font=("Consolas", 9, "bold"), fg="#00FFA3", bg="#101728")
        self.speed_label.pack(anchor="w", pady=4)

        self.transfer_bar = ttk.Progressbar(file_card, mode="determinate")
        self.transfer_bar.pack(fill=tk.X, pady=4)

        btn_send = tk.Button(file_card, text="📁 Send File to Phone (1 GBps)", font=("Segoe UI", 10, "bold"), bg="#00F0FF", fg="#070B14", activebackground="#00D2E0", relief=tk.FLAT, pady=10, command=self.send_file_dialog)
        btn_send.pack(fill=tk.X, pady=(12, 0))

        # Received File Notification Banner
        self.received_frame = tk.Frame(self.root, bg="#18233C", padx=12, pady=8)
        self.received_label = tk.Label(self.received_frame, text="📥 Received files will appear here", font=("Segoe UI", 10, "bold"), fg="#00FFA3", bg="#18233C")
        self.received_label.pack(side=tk.LEFT, padx=6)
        btn_open_folder = tk.Button(self.received_frame, text="📂 Open Downloads", font=("Segoe UI", 9, "bold"), bg="#00FFA3", fg="#070B14", relief=tk.FLAT, command=self.open_downloads_folder)
        btn_open_folder.pack(side=tk.RIGHT, padx=6)
        self.received_frame.pack(fill=tk.X, padx=14, pady=(2, 6))

        # Log Frame
        log_frame = tk.LabelFrame(self.root, text=" 📋 Session Status ", font=("Segoe UI", 10, "bold"), fg="#94A3B8", bg="#070B14", padx=10, pady=6)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 10))

        self.log_text = tk.Text(log_frame, bg="#04060C", fg="#F1F5F9", font=("Consolas", 10), wrap=tk.WORD, relief=tk.FLAT)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        self.log_text.tag_config("cyan", foreground="#00F0FF")
        self.log_text.tag_config("green", foreground="#00FFA3")
        self.log_text.tag_config("yellow", foreground="#FFD600")
        self.log_text.tag_config("red", foreground="#FF4060")
        self.log_text.tag_config("white", foreground="#E2E8F0")

    def _on_phone_auto_discovered(self, ip):
        self.phone_ip = ip
        self.root.after(0, lambda: self._update_discovered_ui(ip))

    def _update_discovered_ui(self, ip):
        self.status_subtext.config(text=f"✓ Auto-Connected to Phone ({ip})", fg="#00FFA3")
        self.badge.config(text="PHONE CONNECTED", fg="#00FFA3")
        self.log(f"[AUTO-DISCOVERED] ✓ Phone found automatically at {ip}! Ready to stream and send.", "green")

    def _on_file_received_from_phone(self, filename, filepath):
        self.root.after(0, lambda: self._update_received_file_ui(filename, filepath))

    def _update_received_file_ui(self, filename, filepath):
        self.received_label.config(text=f"✓ Received from Phone: {filename}")

    def open_downloads_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(DOWNLOADS_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", DOWNLOADS_DIR])
            else:
                subprocess.Popen(["xdg-open", DOWNLOADS_DIR])
        except Exception as e:
            self.log(f"[ERROR] Could not open folder: {e}", "red")

    def log(self, message, color="white"):
        timestamp = time.strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        self.log_text.insert(tk.END, formatted, color)
        self.log_text.see(tk.END)

    def toggle_audio_stream(self):
        if self.audio_streamer and self.audio_streamer.running:
            self.audio_streamer.stop()
            self.audio_streamer = None
            self.btn_audio.config(text="▶ Stream PC Sound to Phone", bg="#7C4DFF", fg="#FFFFFF")
        else:
            self.audio_streamer = DirectAudioStreamer(self.phone_ip, port=AUDIO_PORT, log_callback=self.log)
            self.audio_streamer.start()
            self.btn_audio.config(text="⏹ Stop Sound Stream", bg="#FF4060", fg="#FFFFFF")

    def send_file_dialog(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        self.file_sender = TurboFileSender(self.phone_ip, port=HBSP_FILE_PORT, log_callback=self.log, progress_callback=self._update_progress)
        self.file_sender.send_file(path)

    def _update_progress(self, sent, total, mbps, gbps, ratio):
        pct = (sent / total) * 100 if total > 0 else 0
        self.transfer_bar["value"] = pct
        self.speed_label.config(text=f"Speed: {mbps:.1f} MB/s  ({gbps:.2f} Gbps)  •  Zstd: {ratio:.2f}x")

    def _start_vu_meter_timer(self):
        if self.audio_streamer and self.audio_streamer.running:
            self.vu_bar["value"] = self.audio_streamer.current_volume * 100
        else:
            self.vu_bar["value"] = 0
        self.root.after(100, self._start_vu_meter_timer)


if __name__ == "__main__":
    root = tk.Tk()
    app = P2PwireDesktopApp(root)
    root.mainloop()
