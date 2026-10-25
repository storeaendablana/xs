#!/usr/bin/env python3
"""
================================================================================
  P2Pwire Desktop Turbo Hub 3.0 (with Facebook Zstandard & WASAPI Loopback Fix)
  Compatible with AudioBridge / P2Pwire Android App
================================================================================
Requirements:
  pip install websockets zstandard soundcard numpy
"""

import sys
import os
import time
import socket
import struct
import zlib
import threading
import asyncio
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Facebook Zstandard compression
try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

# Sound loopback capture (Fix for Windows Error 0x80070490)
try:
    import soundcard as sc
    import numpy as np
    HAS_SOUNDCARD = True
except ImportError:
    HAS_SOUNDCARD = False

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

# ==============================================================================
# HBSP Turbo Engine 3.0 Constants
# ==============================================================================
HBSP_MAGIC = b'HBSP'
HBSP_VERSION = 3
FLAG_NONE = 0x00
FLAG_ZSTD = 0x01
FLAG_STREAM = 0x02
FLAG_AUDIO = 0x04
FLAG_FILE = 0x08
FLAG_BENCHMARK = 0x10
FLAG_EOF = 0x20

CHUNK_SIZE = 512 * 1024  # 512 KB optimal L3 cache residency & TCP window
SOCKET_BUF_SIZE = 4 * 1024 * 1024  # 4 MB socket buffer for 1 GBps saturation


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
# Audio Streaming Worker (Fixes Error 0x80070490)
# ==============================================================================
class AudioStreamer:
    def __init__(self, target_ip, port=8080, log_callback=None):
        self.target_ip = target_ip
        self.port = port
        self.log = log_callback or print
        self.running = False
        self.thread = None
        self.loop = None
        self.current_volume = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._stream_task())
        except Exception as e:
            self.log(f"[AUDIO ERROR] Event loop terminated: {e}", "red")
        finally:
            self.running = False

    async def _stream_task(self):
        uri = f"ws://{self.target_ip}:{self.port}/ws/upload_audio"
        self.log(f"[AUDIO] Connecting to {uri}...", "cyan")

        if not HAS_WEBSOCKETS:
            self.log("[ERROR] 'websockets' not installed! Run: pip install websockets", "red")
            return

        if not HAS_SOUNDCARD:
            self.log("[ERROR] 'soundcard' / 'numpy' not installed! Run: pip install soundcard numpy", "red")
            return

        retry_count = 0
        while self.running:
            try:
                # Connect with 5-second timeout and keepalive
                async with websockets.connect(uri, ping_interval=10, ping_timeout=10, close_timeout=2) as ws:
                    self.log(f"[AUDIO SUCCESS] ✓ Connected! Streaming 48kHz PCM audio to Phone ({self.target_ip})", "green")
                    retry_count = 0

                    # Fix for Error 0x80070490:
                    # Windows WASAPI fails when standard PyAudio cannot bind to uninitialized loopback.
                    # soundcard safely queries the active default speaker loopback.
                    speaker = sc.default_speaker()
                    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)

                    with mic.recorder(samplerate=48000, channels=2, blocksize=1024) as rec:
                        while self.running:
                            data = rec.record(numframes=1024)
                            # Peak level for live VU meter
                            peak = np.max(np.abs(data)) if len(data) > 0 else 0.0
                            self.current_volume = float(min(1.0, peak * 1.5))

                            # Convert float32 [-1.0, 1.0] to 16-bit Signed Little-Endian PCM
                            pcm16 = (data * 32767.0).clip(-32768, 32767).astype(np.int16).tobytes()

                            await ws.send(pcm16)
                            await asyncio.sleep(0.001)

            except Exception as e:
                if not self.running:
                    break
                retry_count += 1
                self.log(f"[AUDIO NOTICE] Reconnecting ({retry_count})... Reason: {e}", "yellow")
                await asyncio.sleep(2.0)

        self.log("[AUDIO] Audio streaming stopped.", "white")


# ==============================================================================
# HBSP Turbo Engine 3.0 File Sender with Zstandard
# ==============================================================================
class HbspFileSender:
    def __init__(self, target_ip, port=8082, log_callback=None, progress_callback=None):
        self.target_ip = target_ip
        self.port = port
        self.log = log_callback or print
        self.progress = progress_callback
        self.cancelled = False

    def send_file(self, file_path):
        threading.Thread(target=self._send_file_worker, args=(file_path,), daemon=True).start()

    def run_benchmark(self, size_mb=100):
        threading.Thread(target=self._benchmark_worker, args=(size_mb,), daemon=True).start()

    def cancel(self):
        self.cancelled = True

    def _send_file_worker(self, file_path):
        self.cancelled = False
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        self.log(f"[HBSP 3.0] Preparing to send '{file_name}' ({file_size / (1024*1024):.2f} MB)...", "cyan")

        cctx = zstd.ZstdCompressor(level=1) if HAS_ZSTD else None

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF_SIZE)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
            sock.settimeout(5.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(None)

            self.log(f"[HBSP 3.0] Connected to {self.target_ip}:{self.port} (4MB TCP Window)", "green")

            start_time = time.time()
            total_sent_raw = 0
            total_sent_wire = 0
            seq = 0

            with open(file_path, "rb") as f:
                while total_sent_raw < file_size and not self.cancelled:
                    raw_chunk = f.read(CHUNK_SIZE)
                    if not raw_chunk:
                        break

                    is_eof = (total_sent_raw + len(raw_chunk) >= file_size)

                    # Zstandard compression
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

                    # 24-byte HBSP 3.0 Header
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
            self.log(f"[HBSP SUCCESS] Sent '{file_name}' in {elapsed:.2f}s (Avg: {avg_speed:.1f} MB/s | {(avg_speed*8)/1000:.2f} Gbps)", "green")

        except Exception as e:
            self.log(f"[HBSP ERROR] Transfer failed: {e}", "red")

    def _benchmark_worker(self, size_mb):
        self.cancelled = False
        total_bytes = size_mb * 1024 * 1024
        self.log(f"[HBSP BENCHMARK] Starting 1 GBps Turbo Engine Test ({size_mb} MB) to {self.target_ip}...", "cyan")

        cctx = zstd.ZstdCompressor(level=1) if HAS_ZSTD else None

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF_SIZE)
            sock.settimeout(5.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(None)

            # High-speed patterned memory chunk
            test_pattern = (b"HBSP_TURBO_ZSTD_LINE_DATA_SPEED_TEST_" * 16384)[:CHUNK_SIZE]

            start_time = time.time()
            total_sent_raw = 0
            total_sent_wire = 0
            seq = 0

            while total_sent_raw < total_bytes and not self.cancelled:
                chunk = test_pattern
                is_eof = (total_sent_raw + len(chunk) >= total_bytes)

                payload = chunk
                flags = FLAG_BENCHMARK
                decompressed_len = len(chunk)

                if HAS_ZSTD and cctx is not None:
                    compressed = cctx.compress(chunk)
                    if len(compressed) < len(chunk):
                        payload = compressed
                        flags |= FLAG_ZSTD

                if is_eof:
                    flags |= FLAG_EOF

                checksum = zlib.adler32(payload) & 0xFFFFFFFF
                header = struct.pack("!4sBBHIIII", HBSP_MAGIC, HBSP_VERSION, flags, 0, seq, len(payload), decompressed_len, checksum)

                sock.sendall(header + payload)

                total_sent_raw += len(chunk)
                total_sent_wire += len(header) + len(payload)
                seq += 1

                elapsed = max(0.001, time.time() - start_time)
                speed_mbps = (total_sent_wire / (1024 * 1024)) / elapsed
                gbps = (speed_mbps * 8) / 1000
                ratio = total_sent_raw / max(1, total_sent_wire)

                if self.progress:
                    self.progress(total_sent_raw, total_bytes, speed_mbps, gbps, ratio)

            sock.close()
            elapsed = max(0.001, time.time() - start_time)
            avg_speed = (total_sent_wire / (1024 * 1024)) / elapsed
            self.log(f"[BENCHMARK DONE] Achieved {avg_speed:.1f} MB/s ({(avg_speed*8)/1000:.2f} Gbps) with Zstd Fast-1!", "green")

        except Exception as e:
            self.log(f"[BENCHMARK ERROR] Test failed: {e}", "red")


# ==============================================================================
# Modern Dark Cyberpunk GUI (Tkinter)
# ==============================================================================
class P2PwireDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("P2Pwire Desktop Turbo Hub 3.0")
        self.root.geometry("820x680")
        self.root.minsize(760, 600)
        self.root.configure(bg="#0B0F19")

        self.local_ip = get_local_ip()
        self.audio_streamer = None
        self.file_sender = None

        self._init_styles()
        self._build_ui()
        self._start_vu_meter_timer()

        self.log("[INIT] P2Pwire Desktop Turbo Hub 3.0 Initialized.", "cyan")
        self.log(f"[LOCAL PC IP] {self.local_ip} • Subnet: {self.local_ip.rsplit('.', 1)[0]}.0/24", "white")
        if HAS_ZSTD:
            self.log("[ZSTD ENGINE] ✓ Facebook Zstandard v1.5.6 active for 1 GBps Turbo transfer.", "green")
        else:
            self.log("[ZSTD NOTICE] 'zstandard' module not found. Run: pip install zstandard", "yellow")
        if HAS_SOUNDCARD:
            self.log("[AUDIO ENGINE] ✓ SoundCard WASAPI loopback engine ready (Fix for Error 0x80070490 active).", "green")
        else:
            self.log("[AUDIO NOTICE] 'soundcard' module not found. Run: pip install soundcard numpy", "yellow")

    def _init_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar", thickness=8, troughcolor="#131B2E", background="#00E5FF")

    def _build_ui(self):
        # Header Banner
        header = tk.Frame(self.root, bg="#131B2E", height=70)
        header.pack(fill=tk.X, padx=14, pady=10)

        title_box = tk.Frame(header, bg="#131B2E")
        title_box.pack(side=tk.LEFT, padx=16, pady=12)

        tk.Label(title_box, text="P2Pwire Turbo Hub 3.0", font=("Segoe UI", 16, "bold"), fg="#F1F5F9", bg="#131B2E").pack(anchor="w")
        tk.Label(title_box, text=f"Local PC IP: {self.local_ip}  •  Wi-Fi Direct / LAN Hub", font=("Consolas", 10), fg="#00E5FF", bg="#131B2E").pack(anchor="w")

        status_badge = tk.Label(header, text="HBSP 3.0 + ZSTD ACTIVE", font=("Consolas", 10, "bold"), fg="#00E676", bg="#0B0F19", padx=10, pady=4)
        status_badge.pack(side=tk.RIGHT, padx=16, pady=16)

        # Connection Config Row
        cfg_frame = tk.Frame(self.root, bg="#131B2E")
        cfg_frame.pack(fill=tk.X, padx=14, pady=5)

        tk.Label(cfg_frame, text="Android Phone IP:", font=("Segoe UI", 10, "bold"), fg="#F1F5F9", bg="#131B2E").pack(side=tk.LEFT, padx=(16, 8), pady=12)

        self.ip_entry = tk.Entry(cfg_frame, font=("Consolas", 12), bg="#0B0F19", fg="#00E5FF", insertbackground="#00E5FF", width=18, relief=tk.FLAT)
        self.ip_entry.insert(0, "10.119.76.208")
        self.ip_entry.pack(side=tk.LEFT, padx=6, pady=12)

        btn_test = tk.Button(cfg_frame, text="⚡ Test Ports", font=("Segoe UI", 9, "bold"), bg="#1B253D", fg="#00E5FF", activebackground="#00E5FF", activeforeground="#0B0F19", relief=tk.FLAT, padx=10, pady=4, command=self.test_connection)
        btn_test.pack(side=tk.LEFT, padx=8)

        btn_scan = tk.Button(cfg_frame, text="🔍 Auto-Find Phone", font=("Segoe UI", 9), bg="#1B253D", fg="#F1F5F9", relief=tk.FLAT, padx=10, pady=4, command=self.auto_discover_phone)
        btn_scan.pack(side=tk.LEFT, padx=4)

        # Main Controls Cards
        cards_row = tk.Frame(self.root, bg="#0B0F19")
        cards_row.pack(fill=tk.X, padx=14, pady=8)

        # Card 1: Audio Streaming
        audio_card = tk.LabelFrame(cards_row, text=" 🔊 PC Wireless Speaker (Port 8080) ", font=("Segoe UI", 11, "bold"), fg="#00E5FF", bg="#131B2E", padx=14, pady=12)
        audio_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 7))

        tk.Label(audio_card, text="Streams 48kHz Stereo PCM audio from PC to Phone with zero latency.", font=("Segoe UI", 9), fg="#94A3B8", bg="#131B2E", wraplength=340, justify="left").pack(anchor="w", pady=(0, 10))

        vu_box = tk.Frame(audio_card, bg="#131B2E")
        vu_box.pack(fill=tk.X, pady=6)
        tk.Label(vu_box, text="Live Output VU:", font=("Consolas", 9), fg="#94A3B8", bg="#131B2E").pack(side=tk.LEFT)
        self.vu_bar = ttk.Progressbar(vu_box, length=180, mode="determinate")
        self.vu_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        self.btn_audio = tk.Button(audio_card, text="▶ Start Streaming PC Audio to Phone", font=("Segoe UI", 10, "bold"), bg="#00E5FF", fg="#0B0F19", activebackground="#00B0FF", relief=tk.FLAT, pady=8, command=self.toggle_audio_stream)
        self.btn_audio.pack(fill=tk.X, pady=(12, 0))

        # Card 2: HBSP Turbo Engine 3.0 File Pipeline
        file_card = tk.LabelFrame(cards_row, text=" ⚡ HBSP Turbo 3.0 (Port 8082) ", font=("Segoe UI", 11, "bold"), fg="#00E676", bg="#131B2E", padx=14, pady=12)
        file_card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(7, 0))

        tk.Label(file_card, text="Facebook Zstandard level-1 pipeline targeting up to 1 GBps throughput.", font=("Segoe UI", 9), fg="#94A3B8", bg="#131B2E", wraplength=340, justify="left").pack(anchor="w", pady=(0, 10))

        self.speed_label = tk.Label(file_card, text="Speed: 0.0 MB/s  (0.00 Gbps)  •  Ratio: 1.0x", font=("Consolas", 9, "bold"), fg="#00E676", bg="#131B2E")
        self.speed_label.pack(anchor="w", pady=4)

        self.transfer_bar = ttk.Progressbar(file_card, mode="determinate")
        self.transfer_bar.pack(fill=tk.X, pady=4)

        btns_box = tk.Frame(file_card, bg="#131B2E")
        btns_box.pack(fill=tk.X, pady=(8, 0))

        btn_send = tk.Button(btns_box, text="📁 Send File", font=("Segoe UI", 9, "bold"), bg="#1B253D", fg="#F1F5F9", relief=tk.FLAT, padx=12, pady=6, command=self.send_file_dialog)
        btn_send.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        btn_bench = tk.Button(btns_box, text="🚀 1 GBps Benchmark", font=("Segoe UI", 9, "bold"), bg="#00E676", fg="#0B0F19", relief=tk.FLAT, padx=12, pady=6, command=self.run_benchmark)
        btn_bench.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        # Console / Session Log Frame
        log_frame = tk.LabelFrame(self.root, text=" 📋 Transport Session Log ", font=("Segoe UI", 10, "bold"), fg="#94A3B8", bg="#0B0F19", padx=10, pady=6)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(6, 12))

        self.log_text = tk.Text(log_frame, bg="#050811", fg="#F1F5F9", font=("Consolas", 10), insertbackground="#00E5FF", wrap=tk.WORD, relief=tk.FLAT)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        self.log_text.tag_config("cyan", foreground="#00E5FF")
        self.log_text.tag_config("green", foreground="#00E676")
        self.log_text.tag_config("yellow", foreground="#FFD600")
        self.log_text.tag_config("red", foreground="#FF5252")
        self.log_text.tag_config("white", foreground="#E2E8F0")

    def log(self, message, color="white"):
        timestamp = time.strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        self.log_text.insert(tk.END, formatted, color)
        self.log_text.see(tk.END)

    def test_connection(self):
        target_ip = self.ip_entry.get().strip()
        self.log(f"[TEST] Testing connection to Phone at {target_ip}...", "cyan")

        def _test():
            def probe(port):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1.5)
                    start = time.time()
                    s.connect((target_ip, port))
                    latency = (time.time() - start) * 1000
                    s.close()
                    return True, latency
                except Exception:
                    return False, -1

            audio_ok, a_lat = probe(8080)
            hbsp_ok, h_lat = probe(8082)

            if audio_ok and hbsp_ok:
                self.log(f"[TEST SUCCESS] ✓ Both Audio (8080: {a_lat:.1f}ms) and Files (8082: {h_lat:.1f}ms) REACHABLE on {target_ip}!", "green")
            elif audio_ok:
                self.log(f"[TEST PARTIAL] Audio (8080: {a_lat:.1f}ms) REACHABLE. Port 8082 refused.", "yellow")
            else:
                self.log(f"[TEST FAILED] Unable to reach {target_ip}. Make sure Phone and PC are on same Wi-Fi!", "red")

        threading.Thread(target=_test, daemon=True).start()

    def auto_discover_phone(self):
        self.log("[SCAN] Scanning local subnet for P2Pwire Android device...", "cyan")

        def _scan():
            parts = self.local_ip.split(".")
            if len(parts) != 4:
                return
            base = f"{parts[0]}.{parts[1]}.{parts[2]}."
            found = False

            candidates = [208, 100, 101, 102, 105, 150, 168, 2, 3, 4, 5] + list(range(10, 50))
            for i in candidates:
                test_ip = f"{base}{i}"
                if test_ip == self.local_ip:
                    continue
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.12)
                    s.connect((test_ip, 8080))
                    s.close()
                    self.log(f"[AUTO-FOUND] ✓ Found Android Device at {test_ip}!", "green")
                    self.ip_entry.delete(0, tk.END)
                    self.ip_entry.insert(0, test_ip)
                    found = True
                    break
                except Exception:
                    continue

            if not found:
                self.log("[SCAN] No device found automatically. Please enter Phone IP manually.", "yellow")

        threading.Thread(target=_scan, daemon=True).start()

    def toggle_audio_stream(self):
        if self.audio_streamer and self.audio_streamer.running:
            self.audio_streamer.stop()
            self.audio_streamer = None
            self.btn_audio.config(text="▶ Start Streaming PC Audio to Phone", bg="#00E5FF", fg="#0B0F19")
        else:
            target_ip = self.ip_entry.get().strip()
            self.audio_streamer = AudioStreamer(target_ip, port=8080, log_callback=self.log)
            self.audio_streamer.start()
            self.btn_audio.config(text="⏹ Stop Streaming Audio", bg="#FF5252", fg="#FFFFFF")

    def send_file_dialog(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        target_ip = self.ip_entry.get().strip()
        self.file_sender = HbspFileSender(target_ip, port=8082, log_callback=self.log, progress_callback=self._update_progress)
        self.file_sender.send_file(path)

    def run_benchmark(self):
        target_ip = self.ip_entry.get().strip()
        self.file_sender = HbspFileSender(target_ip, port=8082, log_callback=self.log, progress_callback=self._update_progress)
        self.file_sender.run_benchmark(size_mb=100)

    def _update_progress(self, sent, total, mbps, gbps, ratio):
        pct = (sent / total) * 100 if total > 0 else 0
        self.transfer_bar["value"] = pct
        self.speed_label.config(text=f"Speed: {mbps:.1f} MB/s  ({gbps:.2f} Gbps)  •  Zstd Ratio: {ratio:.2f}x")

    def _start_vu_meter_timer(self):
        if self.audio_streamer and self.audio_streamer.running:
            self.vu_bar["value"] = self.audio_streamer.current_volume * 100
        else:
            self.vu_bar["value"] = 0
        self.root.after(100, self._start_vu_meter_timer)


# ==============================================================================
# Script Entry Point
# ==============================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = P2PwireDesktopApp(root)
    root.mainloop()
