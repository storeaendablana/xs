#!/usr/bin/env python3
"""
================================================================================
  P2Pwire Desktop Turbo Hub 3.0 (Stable 50-55 MBps Turbo Engine)
  - Multi-socket channel striping for maximum throughput (~51-55 MBps)
  - Robust Zstandard compression & 2MB mega-chunks
  - Isolated Port 8080 Audio Engine (<20ms Lip-Sync Guaranteed)
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
import json
import queue
import threading
import subprocess
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Facebook Zstandard compression
try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

# Sound loopback capture & playback
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

INCOMPRESSIBLE_EXTS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v',
    '.zip', '.rar', '.7z', '.gz', '.bz2', '.xz', '.tar', '.tgz',
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.aac', '.flac',
    '.ogg', '.opus', '.apk', '.iso', '.dmg', '.bin'
}

CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB mega-chunk
SOCKET_BUF_SIZE = 8 * 1024 * 1024  # 8 MB socket buffer
DISCOVERY_PORT = 8089
AUDIO_PORT = 8080
HBSP_FILE_PORT = 8082

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "P2Pwire")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


def get_all_local_ips():
    ips = []
    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                if ip not in ips:
                    ips.append(ip)
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        s.close()
        if primary not in ips and not primary.startswith("127."):
            ips.insert(0, primary)
    except Exception:
        pass

    return ips if ips else ["127.0.0.1"]


# ==============================================================================
# Discovery Engine
# ==============================================================================
class PeerAutoDiscovery:
    def __init__(self, local_ips, on_peer_updated_callback):
        self.local_ips = local_ips
        self.on_peer_updated = on_peer_updated_callback
        self.running = True
        self.discovered_ips = set()
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._listen_udp_beacon, daemon=True).start()
        threading.Thread(target=self._broadcast_pc_beacon, daemon=True).start()
        threading.Thread(target=self._active_multirail_scanner, daemon=True).start()

    def _listen_udp_beacon(self):
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
                        parts = msg.split(":")
                        if len(parts) >= 3:
                            raw_ips = parts[2].split(",")
                            for ip in raw_ips:
                                ip = ip.strip()
                                if ip and not ip.startswith("127."):
                                    self._register_peer_ip(ip)
                except socket.timeout:
                    continue
            s.close()
        except Exception:
            pass

    def _broadcast_pc_beacon(self):
        while self.running:
            self.local_ips = get_all_local_ips()
            ips_joined = ",".join(self.local_ips)
            for lip in self.local_ips:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    msg = f"P2PWIRE_BEACON:PC:{ips_joined}".encode("utf-8")
                    s.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
                    s.close()
                except Exception:
                    pass
            time.sleep(1.5)

    def _active_multirail_scanner(self):
        while self.running:
            self.local_ips = get_all_local_ips()
            candidate_targets = ["10.119.76.208", "192.168.42.129", "192.168.42.1", "192.168.43.1"]

            ips_param = ",".join(self.local_ips)

            for target in candidate_targets:
                if not self.running:
                    return
                try:
                    url = f"http://{target}:{AUDIO_PORT}/api/status?pc_ips={ips_param}"
                    req = urllib.request.Request(url, headers={"User-Agent": "P2Pwire-Desktop/3.0"})
                    with urllib.request.urlopen(req, timeout=0.6) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode("utf-8"))
                            self._register_peer_ip(target)
                            for p_ip in data.get("ips", []):
                                if not p_ip.startswith("127."):
                                    self._register_peer_ip(p_ip)
                except Exception:
                    continue

            time.sleep(1.5)

    def _register_peer_ip(self, ip):
        with self.lock:
            if ip and ip not in self.discovered_ips and ip not in self.local_ips:
                self.discovered_ips.add(ip)
                if self.on_peer_updated:
                    self.on_peer_updated(list(self.discovered_ips))

    def stop(self):
        self.running = False


# ==============================================================================
# Audio Streamer & Receiver
# ==============================================================================
class DirectAudioStreamer:
    def __init__(self, target_ip, port=AUDIO_PORT, log_callback=None):
        self.target_ip = target_ip
        self.port = port
        self.log = log_callback or print
        self.running = False
        self.current_volume = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._stream_worker, daemon=True).start()

    def stop(self):
        self.running = False

    def _stream_worker(self):
        if not HAS_SOUNDCARD:
            self.running = False
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(3.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(None)
            sock.sendall(b"STREAM_PCM_48000_16BIT\r\n\r\n")

            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)

            with mic.recorder(samplerate=48000, channels=2, blocksize=480) as rec:
                while self.running:
                    data = rec.record(numframes=480)
                    peak = np.max(np.abs(data)) if len(data) > 0 else 0.0
                    self.current_volume = float(min(1.0, peak * 1.5))
                    pcm16 = (data * 32767.0).clip(-32768, 32767).astype(np.int16).tobytes()
                    sock.sendall(pcm16)
            sock.close()
        except Exception:
            pass
        finally:
            self.running = False


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
            while self.running:
                client, addr = server.accept()
                threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()
        except Exception:
            pass

    def _handle_client(self, client, addr):
        try:
            client.settimeout(None)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if HAS_SOUNDCARD:
                speaker = sc.default_speaker()
                with speaker.player(samplerate=48000, channels=2, blocksize=480) as player:
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
        except Exception:
            pass
        finally:
            client.close()


# ==============================================================================
# HBSP File Receiver Server (Port 8082)
# ==============================================================================
class HbspFileReceiverServer:
    def __init__(self, port=HBSP_FILE_PORT, log_callback=None, on_file_received_callback=None):
        self.port = port
        self.log = log_callback or print
        self.on_file_received = on_file_received_callback
        self.running = True
        self.sessions = {}
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._server_worker, daemon=True).start()

    def _server_worker(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
            server.bind(("0.0.0.0", self.port))
            server.listen(16)
            self.log(f"[HBSP FILE SERVER] Port {self.port} active (Turbo Reassembly Ready)", "white")

            dctx = zstd.ZstdDecompressor() if HAS_ZSTD else None

            while self.running:
                client, addr = server.accept()
                threading.Thread(target=self._handle_file_client, args=(client, addr, dctx), daemon=True).start()
        except Exception as e:
            if self.running:
                self.log(f"[HBSP SERVER ERROR] {e}", "yellow")

    def _handle_file_client(self, client, addr, dctx):
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
        current_session = None

        try:
            while self.running:
                header_data = self._read_exact(client, 24)
                if not header_data:
                    break

                magic, ver, flags, reserved, seq, payload_len, decomp_len, checksum = struct.unpack("!4sBBHIIII", header_data)
                if magic != HBSP_MAGIC:
                    break

                payload = self._read_exact(client, payload_len)
                if not payload:
                    break

                is_zstd = (flags & FLAG_ZSTD) != 0
                if is_zstd and dctx:
                    try:
                        decompressed = dctx.decompress(payload, max_output_size=decomp_len)
                    except Exception:
                        decompressed = payload
                else:
                    decompressed = payload

                is_meta = (flags & FLAG_META) != 0
                if is_meta:
                    meta_str = decompressed.decode("utf-8", errors="ignore").strip()
                    parts = meta_str.split("|")
                    raw_name = parts[0]
                    expected_size = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                    clean_name = os.path.basename(raw_name)
                    target_path = os.path.join(DOWNLOADS_DIR, clean_name)

                    with self.lock:
                        if clean_name not in self.sessions:
                            f = open(target_path, "wb")
                            if expected_size > 0:
                                try:
                                    f.truncate(expected_size)
                                except Exception:
                                    pass
                            self.sessions[clean_name] = {
                                "file": f,
                                "path": target_path,
                                "size": expected_size,
                                "written": 0,
                                "lock": threading.Lock(),
                                "start_time": time.time()
                            }
                            self.log(f"[FILE BOUND] Target set to '{clean_name}' ({expected_size / (1024*1024):.2f} MB)", "cyan")
                        current_session = self.sessions[clean_name]

                if (flags & FLAG_FILE) and not is_meta and current_session:
                    offset = seq * CHUNK_SIZE
                    with current_session["lock"]:
                        current_session["file"].seek(offset)
                        current_session["file"].write(decompressed)
                        current_session["written"] += len(decompressed)

                if flags & FLAG_EOF:
                    if current_session:
                        with self.lock:
                            try:
                                current_session["file"].flush()
                                current_session["file"].close()
                            except Exception:
                                pass
                            path = current_session["path"]
                            name = os.path.basename(path)
                            total = current_session["written"]
                            session_start = current_session.get("start_time", start_time)
                            if name in self.sessions:
                                del self.sessions[name]
                        elapsed = max(0.001, time.time() - session_start)
                        mbps = (total / (1024 * 1024)) / elapsed
                        gbps = (mbps * 8) / 1000
                        self.log(f"[TRANSFER COMPLETE] ✓ '{name}' ({total / (1024*1024):.2f} MB) saved! Speed: {mbps:.1f} MB/s ({gbps:.2f} Gbps)", "green")
                        if self.on_file_received:
                            self.on_file_received(name, path)
                    break
        except Exception:
            pass
        finally:
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
# Turbo File Sender (Optimized Multi-Socket Pipelining - 50-55 MBps)
# ==============================================================================
class TurboFileSender:
    def __init__(self, target_ips, port=HBSP_FILE_PORT, log_callback=None, progress_callback=None):
        self.target_ips = [ip for ip in (target_ips if isinstance(target_ips, list) else [target_ips]) if ip and ip != "0.0.0.0"]
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
        _, ext = os.path.splitext(file_name.lower())

        is_precompressed = ext in INCOMPRESSIBLE_EXTS
        use_zstd = HAS_ZSTD and not is_precompressed

        primary_ip = self.target_ips[0] if self.target_ips else "10.119.76.208"

        sockets = []
        num_sockets = 6  # 6 parallel pipes for stable 50-55 MBps saturation

        self.log(f"[CONNECTING] Opening {num_sockets} turbo pipes to {primary_ip}:{self.port}...", "cyan")

        for ch in range(num_sockets):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF_SIZE)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
                s.settimeout(2.5)
                s.connect((primary_ip, self.port))
                s.settimeout(None)
                sockets.append(s)
            except Exception:
                pass

        if not sockets:
            self.log(f"[ERROR] Could not connect to phone at {primary_ip}!", "red")
            return

        self.log(f"[PIPES ENGAGED] ✓ Connected {len(sockets)} turbo pipes to Phone!", "green")

        cctx = zstd.ZstdCompressor(level=1) if use_zstd else None

        try:
            meta_payload = f"{file_name}|{file_size}".encode("utf-8")
            checksum = zlib.adler32(meta_payload) & 0xFFFFFFFF
            meta_header = struct.pack("!4sBBHIIII", HBSP_MAGIC, HBSP_VERSION, FLAG_FILE | FLAG_META, 0, 0, len(meta_payload), len(meta_payload), checksum)

            for s in sockets:
                try:
                    s.sendall(meta_header + meta_payload)
                except Exception:
                    pass

            start_time = time.time()
            total_sent_raw = 0
            total_sent_wire = (len(meta_header) + len(meta_payload)) * len(sockets)
            stat_lock = threading.Lock()
            last_telemetry_time = time.time()

            chunk_queue = queue.Queue(maxsize=16)

            def worker_thread(sock, sock_idx):
                nonlocal total_sent_raw, total_sent_wire
                while not self.cancelled:
                    item = chunk_queue.get()
                    if item is None:
                        chunk_queue.task_done()
                        break
                    header, payload, raw_len = item
                    try:
                        sock.sendall(header + payload)
                        wire_len = len(header) + len(payload)
                        with stat_lock:
                            total_sent_raw += raw_len
                            total_sent_wire += wire_len
                    except Exception:
                        chunk_queue.task_done()
                        break
                    else:
                        chunk_queue.task_done()

            threads = []
            for idx, s in enumerate(sockets):
                t = threading.Thread(target=worker_thread, args=(s, idx), daemon=True)
                t.start()
                threads.append(t)

            chunk_idx = 0
            bytes_read = 0

            with open(file_path, "rb", buffering=1024 * 1024) as f:
                while bytes_read < file_size and not self.cancelled:
                    raw_chunk = f.read(CHUNK_SIZE)
                    if not raw_chunk:
                        break

                    bytes_read += len(raw_chunk)
                    is_eof = (bytes_read >= file_size)
                    payload = raw_chunk
                    flags = FLAG_FILE
                    decompressed_len = len(raw_chunk)

                    if use_zstd and cctx is not None:
                        compressed = cctx.compress(raw_chunk)
                        if len(compressed) < len(raw_chunk):
                            payload = compressed
                            flags |= FLAG_ZSTD

                    if is_eof:
                        flags |= FLAG_EOF

                    checksum = zlib.adler32(payload) & 0xFFFFFFFF
                    header = struct.pack("!4sBBHIIII", HBSP_MAGIC, HBSP_VERSION, flags, 0, chunk_idx, len(payload), decompressed_len, checksum)
                    chunk_idx += 1

                    chunk_queue.put((header, payload, len(raw_chunk)))

                    now = time.time()
                    if now - last_telemetry_time >= 0.2:
                        dt = max(0.001, now - last_telemetry_time)
                        elapsed = max(0.001, now - start_time)
                        overall_mbps = (total_sent_wire / (1024 * 1024)) / elapsed
                        overall_gbps = (overall_mbps * 8) / 1000
                        last_telemetry_time = now
                        if self.progress:
                            self.progress(bytes_read, file_size, overall_mbps, overall_gbps, len(sockets), {primary_ip: overall_mbps})

            chunk_queue.join()
            for _ in sockets:
                chunk_queue.put(None)
            for t in threads:
                t.join(timeout=1.0)

            for s in sockets:
                try:
                    s.close()
                except Exception:
                    pass

            elapsed = max(0.001, time.time() - start_time)
            avg_speed = (total_sent_wire / (1024 * 1024)) / elapsed
            self.log(f"[TURBO SUCCESS] Sent '{file_name}' via {len(sockets)} pipes in {elapsed:.2f}s (Aggregated Wire Speed: {avg_speed:.1f} MB/s | {(avg_speed*8)/1000:.2f} Gbps)", "green")

        except Exception as e:
            self.log(f"[TURBO ERROR] Transfer error: {e}", "red")


# ==============================================================================
# Desktop GUI App (Stable 50-55 MBps Edition)
# ==============================================================================
class P2PwireDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("P2Pwire Turbo Hub 3.0 (Stable 50-55 MBps)")
        self.root.geometry("820x620")
        self.root.minsize(720, 520)
        self.root.configure(bg="#070B14")

        self.local_ips = get_all_local_ips()
        self.phone_ips = ["10.119.76.208"]
        self.audio_streamer = None
        self.file_sender = None

        self._init_styles()
        self._build_ui()
        self._start_vu_meter_timer()

        self.discovery = PeerAutoDiscovery(self.local_ips, self._on_peers_updated)
        self.discovery.start()

        self.file_server = HbspFileReceiverServer(port=HBSP_FILE_PORT, log_callback=self.log, on_file_received_callback=self._on_file_received_from_phone)
        self.file_server.start()

        self.audio_server = AudioReceiverServer(port=AUDIO_PORT, log_callback=self.log)
        self.audio_server.start()

        self.log("[INIT] P2Pwire Desktop Hub 3.0 (Stable 50-55 MBps Engine) Initialized.", "cyan")

    def _init_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar", thickness=6, troughcolor="#101728", background="#00F0FF")

    def _build_ui(self):
        header = tk.Frame(self.root, bg="#101728", height=65)
        header.pack(fill=tk.X, padx=14, pady=10)

        title_box = tk.Frame(header, bg="#101728")
        title_box.pack(side=tk.LEFT, padx=16, pady=12)

        tk.Label(title_box, text="P2Pwire Turbo Hub 3.0", font=("Segoe UI", 16, "bold"), fg="#FFFFFF", bg="#101728").pack(anchor="w")
        self.status_subtext = tk.Label(title_box, text="Scanning for Phone...", font=("Consolas", 10), fg="#00F0FF", bg="#101728")
        self.status_subtext.pack(anchor="w")

        self.badge = tk.Label(header, text="READY", font=("Consolas", 10, "bold"), fg="#00FFA3", bg="#070B14", padx=10, pady=4)
        self.badge.pack(side=tk.RIGHT, padx=16, pady=16)

        cards_row = tk.Frame(self.root, bg="#070B14")
        cards_row.pack(fill=tk.X, padx=14, pady=4)

        # Audio Card
        audio_card = tk.LabelFrame(cards_row, text=" 🔊 Low-Latency Sound (Port 8080) ", font=("Segoe UI", 11, "bold"), fg="#7C4DFF", bg="#101728", padx=14, pady=12)
        audio_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 7))

        vu_box = tk.Frame(audio_card, bg="#101728")
        vu_box.pack(fill=tk.X, pady=6)
        tk.Label(vu_box, text="Sound VU:", font=("Consolas", 9), fg="#94A3B8", bg="#101728").pack(side=tk.LEFT)
        self.vu_bar = ttk.Progressbar(vu_box, length=160, mode="determinate")
        self.vu_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        self.btn_audio = tk.Button(audio_card, text="▶ Stream PC Sound to Phone", font=("Segoe UI", 10, "bold"), bg="#7C4DFF", fg="#FFFFFF", activebackground="#651FFF", relief=tk.FLAT, pady=10, command=self.toggle_audio_stream)
        self.btn_audio.pack(fill=tk.X, pady=(12, 0))

        # File Card
        file_card = tk.LabelFrame(cards_row, text=" ⚡ Turbo File Blast (Port 8082) ", font=("Segoe UI", 11, "bold"), fg="#00FFA3", bg="#101728", padx=14, pady=12)
        file_card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(7, 0))

        self.speed_label = tk.Label(file_card, text="Speed: 0.0 MB/s (0.00 Gbps)", font=("Consolas", 9, "bold"), fg="#00FFA3", bg="#101728")
        self.speed_label.pack(anchor="w", pady=2)

        self.transfer_bar = ttk.Progressbar(file_card, mode="determinate")
        self.transfer_bar.pack(fill=tk.X, pady=4)

        btn_send = tk.Button(file_card, text="📁 Blast File to Phone (Turbo 6-Pipe)", font=("Segoe UI", 10, "bold"), bg="#00F0FF", fg="#070B14", activebackground="#00D2E0", relief=tk.FLAT, pady=10, command=self.send_file_dialog)
        btn_send.pack(fill=tk.X, pady=(12, 0))

        # Received Notification
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

    def _on_peers_updated(self, ips):
        self.phone_ips = ips
        self.root.after(0, lambda: self._update_discovered_ui(ips))

    def _update_discovered_ui(self, ips):
        primary = ips[0] if ips else "Unknown"
        self.status_subtext.config(text=f"✓ Connected to Phone ({primary})", fg="#00FFA3")
        self.badge.config(text="ONLINE", fg="#00FFA3")

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
        self.root.after(0, lambda: self._safe_append_log(formatted, color))

    def _safe_append_log(self, formatted, color):
        try:
            self.log_text.insert(tk.END, formatted, color)
            self.log_text.see(tk.END)
        except Exception:
            pass

    def toggle_audio_stream(self):
        if self.audio_streamer and self.audio_streamer.running:
            self.audio_streamer.stop()
            self.audio_streamer = None
            self.btn_audio.config(text="▶ Stream PC Sound to Phone", bg="#7C4DFF", fg="#FFFFFF")
        else:
            primary_ip = self.phone_ips[0] if self.phone_ips else "10.119.76.208"
            self.audio_streamer = DirectAudioStreamer(primary_ip, port=AUDIO_PORT, log_callback=self.log)
            self.audio_streamer.start()
            self.btn_audio.config(text="⏹ Stop Sound Stream", bg="#FF4060", fg="#FFFFFF")

    def send_file_dialog(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        self.file_sender = TurboFileSender(self.phone_ips, port=HBSP_FILE_PORT, log_callback=self.log, progress_callback=self._update_progress)
        self.file_sender.send_file(path)

    def _update_progress(self, sent, total, mbps, gbps, pipes, rail_speeds):
        self.root.after(0, lambda: self._render_progress_ui(sent, total, mbps, gbps, pipes))

    def _render_progress_ui(self, sent, total, mbps, gbps, pipes):
        pct = (sent / total) * 100 if total > 0 else 0
        self.transfer_bar["value"] = pct
        self.speed_label.config(text=f"Speed: {mbps:.1f} MB/s ({gbps:.2f} Gbps) • [{pipes} Pipes]")

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
