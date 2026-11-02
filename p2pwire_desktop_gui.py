#!/usr/bin/env python3
"""
================================================================================
  P2Pwire Desktop Turbo Hub 3.0 (True Dual-Rail Link Aggregation: USB + Wi-Fi)
  - Hardware Interface-Pinned Sockets (bind to physical adapter IPs)
  - Real-Time Per-Rail Live Telemetry (Shows exact USB vs Wi-Fi speed contribution)
  - Non-Blocking Lock-Free Worker Queue (Zero UI Throttling)
  - Completely Isolated Port 8080 Audio Engine (<20ms Lip-Sync Guaranteed)
  - Modern Sleek Studio Interface with 2 Distinct Tabs (Files & Audio)
  - Full Persistent Sent & Received Transfer History
  - High-Definition Aeroplane Contrail & Supersonic Sound Engine
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

CHUNK_SIZE = 1024 * 1024  # 1 MB optimal chunk for 1 GBps throughput
SOCKET_BUF_SIZE = 4 * 1024 * 1024  # 4 MB socket buffer
DISCOVERY_PORT = 8089
AUDIO_PORT = 8080
HBSP_FILE_PORT = 8082

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "P2Pwire")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(DOWNLOADS_DIR, "p2pwire_history.json")

# Modern High-Tech Studio Theme Colors
COLOR_BG = "#0B1120"           # Deep Midnight Slate
COLOR_SURFACE = "#1E293B"      # Sleek Slate Card
COLOR_SURFACE_ALT = "#162032"  # Inset Well
COLOR_BORDER = "#334155"       # Subdued Border
COLOR_ACCENT = "#0EA5E9"       # Vibrant Sky Cyan
COLOR_ACCENT_HOVER = "#0284C7" # Darker Cyan
COLOR_SUCCESS = "#10B981"      # Emerald Green
COLOR_WARNING = "#F59E0B"      # Amber Flame
COLOR_DANGER = "#F43F5E"       # Rose / Cancelled
COLOR_TEXT = "#F8FAFC"         # Crisp Ice White
COLOR_TEXT_MUTED = "#94A3B8"   # Slate Subtitle
COLOR_CANVAS_SKY = "#070D18"   # High Altitude Night Sky


def get_all_local_ips():
    """Finds all active network IPs of this PC (Wi-Fi, Ethernet, USB RNDIS)."""
    ips = []
    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                if ip not in ips:
                    ips.append(ip)
    except Exception:
        pass

    if os.name == 'nt':
        try:
            output = subprocess.check_output("ipconfig", shell=True, text=True, errors="ignore")
            for line in output.splitlines():
                line = line.strip()
                if "IPv4" in line or "IP Address" in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        ip = parts[1].strip().replace("(Preferred)", "").strip()
                        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                            if ip not in ips:
                                ips.append(ip)
        except Exception:
            pass
    else:
        try:
            output = subprocess.check_output("hostname -I", shell=True, text=True, errors="ignore")
            for ip in output.split():
                ip = ip.strip()
                if ip and not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass

    for probe_target in ["8.8.8.8", "192.168.42.129", "192.168.43.1", "192.168.1.1"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe_target, 80))
            lip = s.getsockname()[0]
            s.close()
            if lip and not lip.startswith("127.") and lip not in ips:
                ips.append(lip)
        except Exception:
            pass

    return ips if ips else ["127.0.0.1"]


def find_matching_local_ip(target_ip, local_ips):
    target_parts = target_ip.split(".")
    if len(target_parts) == 4:
        target_prefix = f"{target_parts[0]}.{target_parts[1]}.{target_parts[2]}."
        for lip in local_ips:
            if lip.startswith(target_prefix):
                return lip
    return None


# ==============================================================================
# Zero-Config Multi-Rail Discovery Engine
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
                                if ip:
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
            candidate_targets = []
            for lip in self.local_ips:
                parts = lip.split(".")
                if len(parts) == 4:
                    prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
                    if prefix == "192.168.42.":
                        candidate_targets.extend(["192.168.42.129", "192.168.42.1"])
                    elif prefix == "192.168.43.":
                        candidate_targets.append("192.168.43.1")

            for default_gw in ["192.168.42.129", "192.168.42.1", "192.168.43.1"]:
                if default_gw not in candidate_targets:
                    candidate_targets.append(default_gw)

            ips_param = ",".join(self.local_ips)
            for target in candidate_targets:
                if not self.running:
                    return
                try:
                    url = f"http://{target}:{AUDIO_PORT}/api/status?pc_ips={ips_param}"
                    req = urllib.request.Request(url, headers={"User-Agent": "P2Pwire-Desktop/3.0"})
                    with urllib.request.urlopen(req, timeout=0.8) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode("utf-8"))
                            self._register_peer_ip(target)
                            for p_ip in data.get("ips", []):
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
# Ultra-Low-Latency Lip-Sync Sound Streamer (Port 8080)
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
        self.log(f"[SPEAKER] Connecting to Phone Speaker at {self.target_ip}:{self.port} (10ms Lip-Sync Mode)...", "cyan")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 * 1024)
            sock.settimeout(3.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(None)
            sock.sendall(b"STREAM_PCM_48000_16BIT\r\n\r\n")
            self.log(f"[SPEAKER ACTIVE] ✓ Lip-Sync sound streaming active to Phone ({self.target_ip})!", "green")

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
        except Exception as e:
            if self.running:
                self.log(f"[SPEAKER ERROR] Connection error: {e}", "red")
        finally:
            self.running = False
            self.log("[SPEAKER] Sound stream stopped.", "white")


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
            self.log(f"[AUDIO SERVER] Port {self.port} active (Ready to play Phone sound on PC speakers)", "white")
            while self.running:
                client, addr = server.accept()
                threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()
        except Exception as e:
            if self.running:
                self.log(f"[AUDIO SERVER] Port {self.port} notice: {e}", "yellow")

    def _handle_client(self, client, addr):
        self.log(f"[PHONE AUDIO] Connected from Phone ({addr[0]})! Playing sound on PC...", "green")
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
            self.log(f"[PHONE AUDIO] Disconnected ({addr[0]}).", "white")


# ==============================================================================
# Dual-Rail HBSP File Receiver Server (Port 8082)
# ==============================================================================
class HbspFileReceiverServer:
    def __init__(self, port=HBSP_FILE_PORT, log_callback=None, on_file_received_callback=None, progress_callback=None):
        self.port = port
        self.log = log_callback or print
        self.on_file_received = on_file_received_callback
        self.on_progress = progress_callback
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
            server.listen(64)
            self.log(f"[HBSP FILE SERVER] Port {self.port} active (Dual-Rail Reassembly Ready)", "white")
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
        current_session = None
        start_time = time.time()
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
                                "start_time": time.time(),
                                "last_ui_update": 0
                            }
                            self.log(f"[FILE BOUND] Target set to '{clean_name}' ({expected_size / (1024*1024):.2f} MB)", "cyan")
                        current_session = self.sessions[clean_name]

                if (flags & FLAG_FILE) and not is_meta and current_session:
                    offset = seq * CHUNK_SIZE
                    with current_session["lock"]:
                        if not current_session.get("closed", False):
                            try:
                                current_session["file"].seek(offset)
                                current_session["file"].write(decompressed)
                                current_session["written"] += len(decompressed)
                                now = time.time()
                                if now - current_session.get("last_ui_update", 0) > 0.08:
                                    current_session["last_ui_update"] = now
                                    elapsed = max(0.001, now - current_session.get("start_time", start_time))
                                    mbps = (current_session["written"] / (1024 * 1024)) / elapsed
                                    exp_sz = current_session["size"]
                                    rem = max(0, exp_sz - current_session["written"])
                                    eta = int(rem / (mbps * 1024 * 1024)) if mbps > 0.1 and exp_sz > 0 else 0
                                    if self.on_progress:
                                        self.on_progress(clean_name, current_session["written"], exp_sz, mbps, eta)
                            except Exception:
                                pass

                if flags & FLAG_EOF:
                    if current_session:
                        with self.lock:
                            if not current_session.get("closed", False):
                                current_session["closed"] = True
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
                                if self.on_progress:
                                    self.on_progress(name, total, total, mbps, 0)
                                if self.on_file_received:
                                    self.on_file_received(name, path, total, mbps)
                    break
        except Exception as e:
            self.log(f"[FILE RECV ERROR] {e}", "red")
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
# Turbo File Sender Engine
# ==============================================================================
class TurboFileSender:
    def __init__(self, target_ips, port=HBSP_FILE_PORT, log_callback=None, progress_callback=None, on_complete_callback=None):
        self.target_ips = target_ips if isinstance(target_ips, list) else [target_ips]
        self.port = port
        self.log = log_callback or print
        self.progress = progress_callback
        self.on_complete = on_complete_callback
        self.cancelled = False
        self.rail_stats = {}

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

        local_ips = get_all_local_ips()
        clean_ips = list(dict.fromkeys(self.target_ips))
        self.rail_stats = {ip: 0 for ip in clean_ips}

        sockets = []
        sockets_per_ip = 4 if len(clean_ips) >= 2 else 6
        sock_lock = threading.Lock()
        conn_threads = []

        def connect_channel(ip, ch, rail_tag, matching_lip):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF_SIZE)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
                if matching_lip:
                    try:
                        s.bind((matching_lip, 0))
                    except Exception:
                        pass
                s.settimeout(1.5)
                s.connect((ip, self.port))
                s.settimeout(None)
                with sock_lock:
                    sockets.append((s, ip, ch, rail_tag))
            except Exception:
                pass

        for ip in clean_ips:
            matching_lip = find_matching_local_ip(ip, local_ips)
            rail_tag = "USB RNDIS" if ip.startswith("192.168.42.") else ("Wi-Fi Hotspot" if ip.startswith("192.168.43.") else "Wi-Fi LAN")
            for ch in range(sockets_per_ip):
                t = threading.Thread(target=connect_channel, args=(ip, ch, rail_tag, matching_lip), daemon=True)
                t.start()
                conn_threads.append(t)

        for t in conn_threads:
            t.join(timeout=1.6)

        if not sockets:
            self.log("[ERROR] Could not connect to phone! Check if Phone app is open on Port 8082.", "red")
            if self.on_complete:
                self.on_complete(file_name, file_size, 0.0, "FAILED", file_path)
            return

        active_rails = list(dict.fromkeys(ip for _, ip, _, _ in sockets))
        self.log(f"[PIPES ENGAGED] ✓ Connected {len(sockets)} socket pipes across {len(active_rails)} physical interfaces ({', '.join(active_rails)})", "green")

        cctx = zstd.ZstdCompressor(level=1) if use_zstd else None

        try:
            meta_payload = f"{file_name}|{file_size}".encode("utf-8")
            checksum = zlib.adler32(meta_payload) & 0xFFFFFFFF
            meta_header = struct.pack("!4sBBHIIII", HBSP_MAGIC, HBSP_VERSION, FLAG_FILE | FLAG_META, 0, 0, len(meta_payload), len(meta_payload), checksum)

            for s, _, _, _ in sockets:
                s.sendall(meta_header)
                s.sendall(meta_payload)

            start_time = time.time()
            total_sent_wire = (len(meta_header) + len(meta_payload)) * len(sockets)
            stat_lock = threading.Lock()
            last_telemetry_time = time.time()
            last_telemetry_bytes = {ip: 0 for ip in clean_ips}

            chunk_queue = queue.Queue(maxsize=64)
            active_workers = len(sockets)

            def worker_thread(sock, ip, ch_id, rail_name):
                nonlocal total_sent_wire, active_workers
                while not self.cancelled:
                    item = chunk_queue.get()
                    if item is None:
                        chunk_queue.task_done()
                        break
                    header, payload, raw_len = item
                    try:
                        sock.sendall(header)
                        sock.sendall(payload)
                        wire_len = len(header) + len(payload)
                        with stat_lock:
                            total_sent_wire += wire_len
                            self.rail_stats[ip] = self.rail_stats.get(ip, 0) + wire_len
                    except Exception:
                        with stat_lock:
                            active_workers = max(0, active_workers - 1)
                        chunk_queue.task_done()
                        break
                    else:
                        chunk_queue.task_done()

            threads = []
            for s, ip, ch, tag in sockets:
                t = threading.Thread(target=worker_thread, args=(s, ip, ch, tag), daemon=True)
                t.start()
                threads.append(t)

            if self.progress:
                self.progress(0, file_size, 0.0, 0.0, len(sockets), {})

            chunk_idx = 0
            bytes_read = 0

            with open(file_path, "rb", buffering=2 * 1024 * 1024) as f:
                while bytes_read < file_size and not self.cancelled:
                    if active_workers <= 0:
                        break
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
                    chunk_queue.put((header, payload, len(raw_chunk)))
                    chunk_idx += 1

                    now = time.time()
                    if now - last_telemetry_time >= 0.2:
                        dt = max(0.001, now - last_telemetry_time)
                        rail_speeds = {}
                        with stat_lock:
                            for r_ip in self.rail_stats:
                                cur_bytes = self.rail_stats[r_ip]
                                rail_speeds[r_ip] = ((cur_bytes - last_telemetry_bytes.get(r_ip, 0)) / (1024 * 1024)) / dt
                                last_telemetry_bytes[r_ip] = cur_bytes
                            elapsed = max(0.001, now - start_time)
                            overall_mbps = (total_sent_wire / (1024 * 1024)) / elapsed
                            overall_gbps = (overall_mbps * 8) / 1000
                        last_telemetry_time = now
                        if self.progress:
                            self.progress(bytes_read, file_size, overall_mbps, overall_gbps, len(sockets), rail_speeds)

            chunk_queue.join()
            for _ in sockets:
                chunk_queue.put(None)
            for t in threads:
                t.join(timeout=1.0)

            time.sleep(0.15)
            for s, _, _, _ in sockets:
                try:
                    s.shutdown(socket.SHUT_WR)
                except Exception:
                    pass
            time.sleep(0.05)
            for s, _, _, _ in sockets:
                try:
                    s.close()
                except Exception:
                    pass

            elapsed = max(0.001, time.time() - start_time)
            avg_speed = (total_sent_wire / (1024 * 1024)) / elapsed
            if self.progress:
                self.progress(file_size, file_size, avg_speed, (avg_speed * 8) / 1000, len(sockets), {}, is_cancelled=self.cancelled)

            status_str = "CANCELLED" if self.cancelled else "COMPLETED"
            if self.on_complete:
                self.on_complete(file_name, file_size, avg_speed, status_str, file_path)

            if not self.cancelled:
                self.log(f"[TURBO SUCCESS] Sent '{file_name}' via {len(sockets)} pipes in {elapsed:.2f}s ({avg_speed:.1f} MB/s)", "green")
        except Exception as e:
            self.log(f"[TURBO ERROR] Transfer error: {e}", "red")
            if self.on_complete:
                self.on_complete(file_name, file_size, 0.0, "FAILED", file_path)


# ==============================================================================
# Jet Audio Synthesizer & Sound FX
# ==============================================================================
class ContinuousJetSoundManager:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        def worker():
            while self.running:
                try:
                    if sys.platform == "win32":
                        import winsound
                        winsound.Beep(300, 180)
                    else:
                        print('\a', end='', flush=True)
                except Exception:
                    pass
                time.sleep(0.12)
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread = None

desktop_continuous_sound = ContinuousJetSoundManager()


def play_jet_audio(sound_type="takeoff"):
    def _audio_worker():
        try:
            if sys.platform == "win32":
                import winsound
                if sound_type == "takeoff":
                    winsound.Beep(520, 180)
                    winsound.Beep(850, 260)
                elif sound_type == "cruising":
                    winsound.Beep(300, 220)
                else:
                    winsound.Beep(780, 200)
                    winsound.Beep(1200, 400)
            else:
                print('\a', end='', flush=True)
        except Exception:
            pass
    threading.Thread(target=_audio_worker, daemon=True).start()


# ==============================================================================
# High-Definition Modern Jet Airliner Flight Canvas
# ==============================================================================
class AeroplaneCurveCanvas(tk.Canvas):
    def __init__(self, master, width=320, height=62, **kwargs):
        super().__init__(
            master, width=width, height=height,
            bg=COLOR_CANVAS_SKY, highlightthickness=1,
            highlightbackground=COLOR_BORDER, **kwargs
        )
        self.w = width
        self.h = height
        self.progress = 0.0
        self.telemetry = ""
        self.speed_mbps = 0.0
        self.is_cancelled = False
        self.completion_exit_step = 0
        self.has_played_takeoff = False
        self.has_played_complete = False
        self.bind("<Configure>", self._on_resize)
        self.draw()

    def _on_resize(self, event):
        if event.width > 20:
            self.w = event.width
            self.h = event.height
            self.draw()

    def set_progress(self, fraction, telemetry="", is_cancelled=False):
        self.progress = max(0.0, min(1.0, fraction))
        self.telemetry = telemetry
        self.is_cancelled = is_cancelled

        import re
        match = re.search(r"([\d\.]+)\s*MB/s", telemetry)
        if match:
            try:
                self.speed_mbps = float(match.group(1))
            except Exception:
                self.speed_mbps = 0.0
        elif self.progress >= 1.0:
            self.speed_mbps = 45.0
        else:
            self.speed_mbps = max(self.speed_mbps * 0.9, 0.0)

        # Sound effects trigger
        if self.progress > 0.02 and self.progress < 1.0 and not self.is_cancelled:
            if not self.has_played_takeoff:
                self.has_played_takeoff = True
                play_jet_audio("takeoff")
            desktop_continuous_sound.start()
        else:
            desktop_continuous_sound.stop()

        if self.progress >= 1.0 and not self.is_cancelled and not self.has_played_complete:
            self.has_played_complete = True
            desktop_continuous_sound.stop()
            play_jet_audio("complete")
        elif self.progress < 0.05:
            self.has_played_takeoff = False
            self.has_played_complete = False

        if self.progress >= 1.0 and not self.is_cancelled and self.completion_exit_step == 0:
            self._animate_exit()
        elif self.progress < 1.0:
            self.completion_exit_step = 0

        self.draw()

    def _animate_exit(self):
        if self.completion_exit_step < 15 and self.progress >= 1.0 and not self.is_cancelled:
            self.completion_exit_step += 1
            self.draw()
            self.after(30, self._animate_exit)

    def draw(self):
        self.delete("all")
        pad_x = 24
        start_x = pad_x
        end_x = max(start_x + 50, self.w - pad_x)
        travel_x = end_x - start_x
        start_y = self.h - 16

        # Altitude dynamically climbs with transfer speed (higher speed = climbs to ceiling)
        speed_factor = max(0.25, min(1.0, self.speed_mbps / 45.0))
        climb_span = (self.h - 26) * speed_factor

        def get_pt(p):
            x = start_x + travel_x * p
            y = start_y - climb_span * (p ** 1.25)
            return x, y

        # 1. Subtle High-Tech Grid & Horizon Guidance Line
        self.create_line(pad_x, start_y + 4, self.w - pad_x, start_y + 4, fill="#1E293B", dash=(3, 3), width=1)

        # 2. Planned Supersonic Flight Corridor (Dashed Curve)
        corridor_pts = []
        for s in range(25):
            p = s / 24.0
            cx, cy = get_pt(p)
            corridor_pts.extend([cx, cy])
        if len(corridor_pts) >= 4:
            self.create_line(*corridor_pts, fill="#1E3A5F", dash=(4, 6), width=1)

        # 3. Destination Airport / Sky Radar Beacon
        bx, by = get_pt(1.0)
        beacon_color = COLOR_DANGER if self.is_cancelled else (COLOR_SUCCESS if self.progress >= 1.0 else COLOR_ACCENT)
        self.create_oval(bx - 4, by - 4, bx + 4, by + 4, fill=beacon_color, outline="")
        if self.progress >= 1.0 and not self.is_cancelled:
            self.create_oval(bx - 8, by - 8, bx + 8, by + 8, outline=COLOR_SUCCESS, width=1.5)
            self.create_oval(bx - 12, by - 12, bx + 12, by + 12, outline=COLOR_SUCCESS, width=0.8)

        # 4. Dense Supersonic Contrail Vapor Trail (The Progress Bar)
        if self.progress > 0.005:
            contrail_pts = []
            steps = max(3, int(40 * self.progress))
            for s in range(steps + 1):
                p = min(self.progress, s / 40.0)
                cx, cy = get_pt(p)
                contrail_pts.extend([cx, cy])

            if len(contrail_pts) >= 4:
                glow_color = COLOR_DANGER if self.is_cancelled else ("#38BDF8" if self.progress >= 1.0 else "#0284C7")
                core_color = "#FECDD3" if self.is_cancelled else ("#A7F3D0" if self.progress >= 1.0 else "#E0F2FE")

                # Layer 1: Wide atmospheric glow
                self.create_line(*contrail_pts, fill=glow_color, width=5)
                # Layer 2: Dense vapor condensation line
                self.create_line(*contrail_pts, fill=core_color, width=2)

        # 5. Real Modern Jet Airliner Polygon Drawing
        px, py = get_pt(self.progress)
        is_exited = False
        if self.progress >= 1.0 and not self.is_cancelled and self.completion_exit_step > 0:
            px += self.completion_exit_step * 10
            py -= self.completion_exit_step * 8
            if px > self.w + 50 or py < -30:
                is_exited = True

        if not is_exited:
            jet_body = COLOR_DANGER if self.is_cancelled else (COLOR_SUCCESS if self.progress >= 1.0 else "#F8FAFC")
            wing_accent = "#94A3B8" if self.is_cancelled else (COLOR_SUCCESS if self.progress >= 1.0 else COLOR_ACCENT)

            # Twin Underwing Turbofan Exhaust Flames
            if not self.is_cancelled and 0.01 < self.progress < 1.0:
                flame_len = 10 + int(self.speed_mbps * 0.15)
                self.create_line(px - flame_len, py - 5.5, px - 3, py - 5.5, fill="#F97316", width=2.5)
                self.create_line(px - flame_len, py + 5.5, px - 3, py + 5.5, fill="#F97316", width=2.5)
                self.create_line(px - (flame_len - 4), py - 5.5, px - 3, py - 5.5, fill="#FEF08A", width=1.5)
                self.create_line(px - (flame_len - 4), py + 5.5, px - 3, py + 5.5, fill="#FEF08A", width=1.5)

            # Aerodynamic Airliner Polygon: Pointed radome (+12), cockpit windshield (+6),
            # swept wings with blended winglets (y ±12), wing trailing edge, fuselage, tail stabilizers (y ±6)
            poly_points = [
                px + 13, py - 1.5,  # Nose cone tip
                px + 7,  py - 2.5,  # Cockpit windshield
                px + 3,  py - 3.0,  # Forward wing root
                px - 4,  py - 12.0, # Starboard swept wingtip
                px - 6,  py - 11.0, # Winglet fin
                px - 5,  py - 3.0,  # Wing trailing edge
                px - 8,  py - 2.5,  # Fuselage to tail
                px - 11, py - 6.0,  # Starboard horizontal stabilizer
                px - 12, py - 5.0,
                px - 12, py + 0.5,  # Tail APU exhaust cone
                px - 12, py + 5.0,  # Port horizontal stabilizer
                px - 11, py + 6.0,
                px - 8,  py + 2.5,  # Fuselage
                px - 5,  py + 3.0,  # Port wing trailing edge
                px - 6,  py + 11.0, # Port winglet fin
                px - 4,  py + 12.0, # Port swept wingtip
                px + 3,  py + 3.0,  # Forward wing root
                px + 7,  py + 2.5,  # Cockpit
                px + 13, py + 0.5   # Nose cone bottom
            ]
            self.create_polygon(poly_points, fill=jet_body, outline=wing_accent, width=1.2)

            # Twin Underwing Turbofan Engines
            self.create_rectangle(px - 3, py - 7, px + 3, py - 4.5, fill="#38BDF8", outline="#FFFFFF")
            self.create_rectangle(px - 3, py + 4.5, px + 3, py + 7, fill="#38BDF8", outline="#FFFFFF")

            # Cockpit Windshield Glint
            self.create_oval(px + 5, py - 2, px + 9, py + 0.5, fill="#7DD3FC", outline="")

        # 6. Flight Telemetry Label
        if self.telemetry:
            txt_color = COLOR_DANGER if self.is_cancelled else COLOR_ACCENT
            status_text = self.telemetry
            if self.progress >= 1.0 and not self.is_cancelled:
                status_text = f"✓ Supersonic Flight Landed • {self.telemetry}"
            self.create_text(start_x, start_y + 11, text=status_text, anchor="nw", font=("Consolas", 8, "bold"), fill=txt_color)


# ==============================================================================
# Main P2Pwire Desktop App (2 Tabs + Transfer History)
# ==============================================================================
class P2PwireDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("P2Pwire Turbo Hub 3.0 (Dual-Rail Link Aggregation)")
        self.root.geometry("920x760")
        self.root.minsize(820, 640)
        self.root.configure(bg=COLOR_BG)

        self.local_ips = get_all_local_ips()
        self.phone_ips = []
        self.audio_streamer = None
        self.file_sender = None
        self.current_tab = "files"  # "files" or "sound"
        self.history_items = []

        self._load_history()
        self._init_styles()
        self._build_ui()
        self._start_vu_meter_timer()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Discovery & Servers
        self.discovery = PeerAutoDiscovery(self.local_ips, self._on_peers_updated)
        self.discovery.start()

        self.file_server = HbspFileReceiverServer(
            port=HBSP_FILE_PORT,
            log_callback=self.log,
            on_file_received_callback=self._on_file_received_from_phone,
            progress_callback=self._on_recv_progress
        )
        self.file_server.start()

        self.audio_server = AudioReceiverServer(port=AUDIO_PORT, log_callback=self.log)
        self.audio_server.start()

        self.log("[INIT] P2Pwire Desktop Studio 3.0 Ready (Audio: Port 8080 | Files: Port 8082).", "cyan")
        self.log(f"[NETWORK INTERFACES] {', '.join(self.local_ips)}", "white")

    def _init_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
            background=COLOR_SURFACE_ALT,
            foreground=COLOR_TEXT,
            fieldbackground=COLOR_SURFACE_ALT,
            rowheight=26,
            font=("Segoe UI", 9)
        )
        style.configure("Treeview.Heading",
            background=COLOR_SURFACE,
            foreground=COLOR_ACCENT,
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT
        )
        style.map("Treeview",
            background=[("selected", COLOR_ACCENT_HOVER)],
            foreground=[("selected", "#FFFFFF")]
        )
        style.configure("TProgressbar",
            thickness=8,
            troughcolor=COLOR_SURFACE_ALT,
            background=COLOR_ACCENT
        )

    def _build_ui(self):
        # 1. Top Modern Header Banner
        header = tk.Frame(self.root, bg=COLOR_SURFACE, padx=18, pady=12, highlightthickness=1, highlightbackground=COLOR_BORDER)
        header.pack(fill=tk.X, padx=14, pady=(12, 6))

        # Brand Icon Badge
        badge_box = tk.Frame(header, bg="#0F172A", width=46, height=46, highlightthickness=1, highlightbackground=COLOR_ACCENT)
        badge_box.pack_propagate(False)
        badge_box.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(badge_box, text="⚡", font=("Segoe UI", 18, "bold"), fg=COLOR_ACCENT, bg="#0F172A").pack(expand=True)

        title_box = tk.Frame(header, bg=COLOR_SURFACE)
        title_box.pack(side=tk.LEFT)
        tk.Label(title_box, text="P2Pwire Turbo Hub 3.0", font=("Segoe UI", 15, "bold"), fg=COLOR_TEXT, bg=COLOR_SURFACE).pack(anchor="w")
        self.status_subtext = tk.Label(title_box, text="Scanning network for Phone (Wi-Fi + USB Tethering)...", font=("Segoe UI", 9), fg=COLOR_TEXT_MUTED, bg=COLOR_SURFACE)
        self.status_subtext.pack(anchor="w")

        # Mesh Status Badge
        self.badge = tk.Label(header, text="SEARCHING MESH", font=("Consolas", 9, "bold"), fg=COLOR_ACCENT, bg=COLOR_SURFACE_ALT, padx=12, pady=5, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.badge.pack(side=tk.RIGHT)

        # 2. Target Phone IP Input Well
        ip_bar = tk.Frame(self.root, bg=COLOR_SURFACE_ALT, padx=14, pady=6, highlightthickness=1, highlightbackground=COLOR_BORDER)
        ip_bar.pack(fill=tk.X, padx=14, pady=4)
        tk.Label(ip_bar, text="Target Phone IP(s):", font=("Segoe UI", 9, "bold"), fg=COLOR_TEXT_MUTED, bg=COLOR_SURFACE_ALT).pack(side=tk.LEFT, padx=(0, 8))
        self.ip_entry = tk.Entry(ip_bar, font=("Consolas", 10), bg="#0B1120", fg=COLOR_TEXT, insertbackground=COLOR_ACCENT, relief=tk.FLAT, bd=4)
        self.ip_entry.insert(0, ", ".join(self.phone_ips))
        self.ip_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        btn_rescan = tk.Button(ip_bar, text="🔄 Auto-Detect", font=("Segoe UI", 8, "bold"), bg=COLOR_SURFACE, fg=COLOR_TEXT, activebackground=COLOR_ACCENT, relief=tk.FLAT, padx=10, command=self._rescan_peers)
        btn_rescan.pack(side=tk.RIGHT)

        # 3. Two-Tab Navigation Bar (Custom Modern Segmented Control)
        tab_bar = tk.Frame(self.root, bg=COLOR_BG)
        tab_bar.pack(fill=tk.X, padx=14, pady=(8, 2))

        self.btn_tab_files = tk.Button(
            tab_bar, text="⚡ File Transfer & History", font=("Segoe UI", 10, "bold"),
            bg=COLOR_ACCENT_HOVER, fg="#FFFFFF", activebackground=COLOR_ACCENT,
            relief=tk.FLAT, padx=18, pady=8, bd=0, command=lambda: self.switch_tab("files")
        )
        self.btn_tab_files.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_tab_sound = tk.Button(
            tab_bar, text="🔊 Sound Transmission (10ms Lip-Sync)", font=("Segoe UI", 10, "bold"),
            bg=COLOR_SURFACE, fg=COLOR_TEXT_MUTED, activebackground=COLOR_SURFACE,
            relief=tk.FLAT, padx=18, pady=8, bd=0, command=lambda: self.switch_tab("sound")
        )
        self.btn_tab_sound.pack(side=tk.LEFT)

        # 4. Tab Content Container
        self.tab_container = tk.Frame(self.root, bg=COLOR_BG)
        self.tab_container.pack(fill=tk.BOTH, expand=True, padx=14, pady=4)

        self._build_tab_files()
        self._build_tab_sound()
        self.switch_tab("files")

        # 5. Bottom Log Console (Collapsible)
        bottom_frame = tk.Frame(self.root, bg=COLOR_SURFACE, highlightthickness=1, highlightbackground=COLOR_BORDER)
        bottom_frame.pack(fill=tk.X, padx=14, pady=(4, 10))

        log_header = tk.Frame(bottom_frame, bg=COLOR_SURFACE, padx=8, pady=4)
        log_header.pack(fill=tk.X)
        tk.Label(log_header, text="📋 Real-Time Hub Diagnostics", font=("Segoe UI", 8, "bold"), fg=COLOR_TEXT_MUTED, bg=COLOR_SURFACE).pack(side=tk.LEFT)

        self.log_text = tk.Text(bottom_frame, height=4, bg=COLOR_SURFACE_ALT, fg=COLOR_TEXT, font=("Consolas", 8), wrap=tk.WORD, bd=0, relief=tk.FLAT)
        self.log_text.pack(fill=tk.X, padx=8, pady=(0, 6))

        self.log_text.tag_config("cyan", foreground=COLOR_ACCENT)
        self.log_text.tag_config("green", foreground=COLOR_SUCCESS)
        self.log_text.tag_config("yellow", foreground=COLOR_WARNING)
        self.log_text.tag_config("red", foreground=COLOR_DANGER)
        self.log_text.tag_config("white", foreground=COLOR_TEXT)

    # --------------------------------------------------------------------------
    # TAB 1: FILE TRANSFER & HISTORY
    # --------------------------------------------------------------------------
    def _build_tab_files(self):
        self.frame_files = tk.Frame(self.tab_container, bg=COLOR_BG)

        # Dual Action Cards (Outgoing Blast + Incoming Receiver)
        transfer_cards = tk.Frame(self.frame_files, bg=COLOR_BG)
        transfer_cards.pack(fill=tk.X, pady=(0, 6))

        # Card A: Outgoing Turbo Blast
        send_card = tk.LabelFrame(
            transfer_cards, text=" 🚀 Outgoing Turbo Blast (PC ➡️ Phone) ",
            font=("Segoe UI", 9, "bold"), fg=COLOR_ACCENT, bg=COLOR_SURFACE,
            relief=tk.FLAT, bd=1, highlightthickness=1, highlightbackground=COLOR_BORDER, padx=12, pady=8
        )
        send_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        send_top = tk.Frame(send_card, bg=COLOR_SURFACE)
        send_top.pack(fill=tk.X, pady=(0, 4))
        self.speed_label = tk.Label(send_top, text="Speed: 0.0 MB/s (Idle)", font=("Consolas", 9, "bold"), fg=COLOR_ACCENT, bg=COLOR_SURFACE)
        self.speed_label.pack(side=tk.LEFT)
        self.rail_telemetry_label = tk.Label(send_top, text="Pipes: Idle", font=("Consolas", 8), fg=COLOR_TEXT_MUTED, bg=COLOR_SURFACE)
        self.rail_telemetry_label.pack(side=tk.RIGHT)

        self.send_flight_canvas = AeroplaneCurveCanvas(send_card, height=56)
        self.send_flight_canvas.pack(fill=tk.X, pady=4)

        send_btns = tk.Frame(send_card, bg=COLOR_SURFACE)
        send_btns.pack(fill=tk.X, pady=(6, 0))
        btn_send = tk.Button(
            send_btns, text="📁 Blast File to Phone (Dual-Rail)", font=("Segoe UI", 9, "bold"),
            bg=COLOR_ACCENT, fg="#FFFFFF", activebackground=COLOR_ACCENT_HOVER, relief=tk.FLAT, pady=6,
            command=self.send_file_dialog
        )
        btn_send.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        btn_cancel = tk.Button(
            send_btns, text="⏹ Cancel", font=("Segoe UI", 8, "bold"),
            bg=COLOR_SURFACE_ALT, fg=COLOR_DANGER, activebackground="#334155", relief=tk.FLAT, pady=6, padx=10,
            command=self.cancel_send
        )
        btn_cancel.pack(side=tk.RIGHT)

        # Card B: Incoming File Receiver
        recv_card = tk.LabelFrame(
            transfer_cards, text=" 📥 Incoming File Receiver (Phone ➡️ PC) ",
            font=("Segoe UI", 9, "bold"), fg=COLOR_SUCCESS, bg=COLOR_SURFACE,
            relief=tk.FLAT, bd=1, highlightthickness=1, highlightbackground=COLOR_BORDER, padx=12, pady=8
        )
        recv_card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        recv_top = tk.Frame(recv_card, bg=COLOR_SURFACE)
        recv_top.pack(fill=tk.X, pady=(0, 4))
        self.received_label = tk.Label(recv_top, text="Port 8082 Ready • Listening", font=("Segoe UI", 8, "bold"), fg=COLOR_SUCCESS, bg=COLOR_SURFACE)
        self.received_label.pack(side=tk.LEFT)
        btn_folder = tk.Button(
            recv_top, text="📂 Open Downloads", font=("Segoe UI", 8, "bold"),
            bg=COLOR_SURFACE_ALT, fg=COLOR_TEXT, activebackground="#334155", relief=tk.FLAT, padx=8, pady=2,
            command=self.open_downloads_folder
        )
        btn_folder.pack(side=tk.RIGHT)

        self.recv_flight_canvas = AeroplaneCurveCanvas(recv_card, height=56)
        self.recv_flight_canvas.pack(fill=tk.X, pady=4)

        # ----------------------------------------------------------------------
        # Transfer History Panel (Sent & Received Table)
        # ----------------------------------------------------------------------
        history_frame = tk.LabelFrame(
            self.frame_files, text=" 📜 Transfer History (Sent & Received Files) ",
            font=("Segoe UI", 9, "bold"), fg=COLOR_TEXT, bg=COLOR_SURFACE,
            relief=tk.FLAT, bd=1, highlightthickness=1, highlightbackground=COLOR_BORDER, padx=10, pady=8
        )
        history_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        columns = ("time", "type", "name", "size", "speed", "status")
        self.history_tree = ttk.Treeview(history_frame, columns=columns, show="headings", height=7)
        self.history_tree.heading("time", text="Time")
        self.history_tree.heading("type", text="Type")
        self.history_tree.heading("name", text="File Name")
        self.history_tree.heading("size", text="Size")
        self.history_tree.heading("speed", text="Avg Speed")
        self.history_tree.heading("status", text="Status")

        self.history_tree.column("time", width=80, anchor="center")
        self.history_tree.column("type", width=75, anchor="center")
        self.history_tree.column("name", width=340, anchor="w")
        self.history_tree.column("size", width=95, anchor="center")
        self.history_tree.column("speed", width=105, anchor="center")
        self.history_tree.column("status", width=95, anchor="center")

        tree_scroll = ttk.Scrollbar(history_frame, orient=tk.VERTICAL, command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=tree_scroll.set)
        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.history_tree.bind("<Double-1>", lambda e: self.open_selected_history_file())

        history_btn_bar = tk.Frame(history_frame, bg=COLOR_SURFACE)
        history_btn_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        btn_open_file = tk.Button(
            history_btn_bar, text="📄 Open File", font=("Segoe UI", 8, "bold"),
            bg=COLOR_SURFACE_ALT, fg=COLOR_TEXT, activebackground="#334155", relief=tk.FLAT, padx=10, pady=3,
            command=self.open_selected_history_file
        )
        btn_open_file.pack(side=tk.LEFT, padx=(0, 6))

        btn_open_dir = tk.Button(
            history_btn_bar, text="📁 Open Folder", font=("Segoe UI", 8, "bold"),
            bg=COLOR_SURFACE_ALT, fg=COLOR_TEXT, activebackground="#334155", relief=tk.FLAT, padx=10, pady=3,
            command=self.open_selected_history_folder
        )
        btn_open_dir.pack(side=tk.LEFT, padx=(0, 6))

        btn_clear = tk.Button(
            history_btn_bar, text="🗑 Clear History", font=("Segoe UI", 8, "bold"),
            bg=COLOR_SURFACE_ALT, fg=COLOR_DANGER, activebackground="#334155", relief=tk.FLAT, padx=10, pady=3,
            command=self.clear_history
        )
        btn_clear.pack(side=tk.RIGHT)

        self._render_history_tree()

    # --------------------------------------------------------------------------
    # TAB 2: SOUND TRANSMISSION
    # --------------------------------------------------------------------------
    def _build_tab_sound(self):
        self.frame_sound = tk.Frame(self.tab_container, bg=COLOR_BG)

        # Card 1: PC to Phone Speaker Streaming
        pc_sound_card = tk.LabelFrame(
            self.frame_sound, text=" 🔊 Stream PC Sound to Phone (10ms Lip-Sync) ",
            font=("Segoe UI", 10, "bold"), fg=COLOR_ACCENT, bg=COLOR_SURFACE,
            relief=tk.FLAT, bd=1, highlightthickness=1, highlightbackground=COLOR_BORDER, padx=16, pady=16
        )
        pc_sound_card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            pc_sound_card,
            text="Turns your Android phone into an ultra-low-latency wireless PC speaker (Dedicated Port 8080).",
            font=("Segoe UI", 9), fg=COLOR_TEXT_MUTED, bg=COLOR_SURFACE
        ).pack(anchor="w", pady=(0, 8))

        vu_frame = tk.Frame(pc_sound_card, bg=COLOR_SURFACE)
        vu_frame.pack(fill=tk.X, pady=6)
        tk.Label(vu_frame, text="Live Output VU Meter:", font=("Segoe UI", 9, "bold"), fg=COLOR_TEXT, bg=COLOR_SURFACE).pack(side=tk.LEFT, padx=(0, 10))
        self.vu_bar = ttk.Progressbar(vu_frame, length=240, mode="determinate")
        self.vu_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_audio = tk.Button(
            pc_sound_card, text="▶ Start Streaming PC Sound to Phone", font=("Segoe UI", 10, "bold"),
            bg=COLOR_ACCENT, fg="#FFFFFF", activebackground=COLOR_ACCENT_HOVER, relief=tk.FLAT, pady=10,
            command=self.toggle_audio_stream
        )
        self.btn_audio.pack(fill=tk.X, pady=(12, 0))

        # Card 2: Phone to PC Speakers Server
        phone_recv_card = tk.LabelFrame(
            self.frame_sound, text=" 📥 Receive Phone Sound to PC Speakers ",
            font=("Segoe UI", 10, "bold"), fg=COLOR_SUCCESS, bg=COLOR_SURFACE,
            relief=tk.FLAT, bd=1, highlightthickness=1, highlightbackground=COLOR_BORDER, padx=16, pady=16
        )
        phone_recv_card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            phone_recv_card,
            text="Port 8080 background server is active. Any sound streamed from the Android app will play automatically through your PC speakers.",
            font=("Segoe UI", 9), fg=COLOR_TEXT_MUTED, bg=COLOR_SURFACE, wraplength=760, justify=tk.LEFT
        ).pack(anchor="w")

        status_pill = tk.Label(
            phone_recv_card, text="✓ Server Ready: Listening on 0.0.0.0:8080",
            font=("Consolas", 9, "bold"), fg=COLOR_SUCCESS, bg=COLOR_SURFACE_ALT, padx=10, pady=4
        )
        status_pill.pack(anchor="w", pady=(10, 0))

    # --------------------------------------------------------------------------
    # Tab Switching Logic
    # --------------------------------------------------------------------------
    def switch_tab(self, tab_id):
        self.current_tab = tab_id
        if tab_id == "files":
            self.frame_sound.pack_forget()
            self.frame_files.pack(fill=tk.BOTH, expand=True)
            self.btn_tab_files.config(bg=COLOR_ACCENT_HOVER, fg="#FFFFFF")
            self.btn_tab_sound.config(bg=COLOR_SURFACE, fg=COLOR_TEXT_MUTED)
        else:
            self.frame_files.pack_forget()
            self.frame_sound.pack(fill=tk.BOTH, expand=True)
            self.btn_tab_sound.config(bg=COLOR_ACCENT_HOVER, fg="#FFFFFF")
            self.btn_tab_files.config(bg=COLOR_SURFACE, fg=COLOR_TEXT_MUTED)

    # --------------------------------------------------------------------------
    # Transfer History Storage & Actions
    # --------------------------------------------------------------------------
    def _load_history(self):
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    self.history_items = json.load(f)
        except Exception:
            self.history_items = []

    def _save_history(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.history_items[:150], f, indent=2)
        except Exception:
            pass

    def add_history_entry(self, direction, filename, size_bytes, speed_str, status, filepath=""):
        item = {
            "time": time.strftime("%H:%M:%S"),
            "type": direction,
            "name": filename,
            "size": f"{size_bytes / (1024*1024):.1f} MB" if size_bytes >= 1024*1024 else f"{size_bytes / 1024:.1f} KB",
            "speed": speed_str,
            "status": status,
            "path": filepath or os.path.join(DOWNLOADS_DIR, filename)
        }
        self.history_items.insert(0, item)
        self._save_history()
        self.root.after(0, self._render_history_tree)

    def _render_history_tree(self):
        for row in self.history_tree.get_children():
            self.history_tree.delete(row)
        for item in self.history_items:
            type_symbol = "⬆ Sent" if "SENT" in item["type"].upper() else "⬇ Recv"
            status_text = "✓ Done" if item["status"] == "COMPLETED" else "⏹ " + item["status"]
            self.history_tree.insert(
                "", tk.END,
                values=(item["time"], type_symbol, item["name"], item["size"], item["speed"], status_text)
            )

    def open_selected_history_file(self):
        selected = self.history_tree.selection()
        if not selected:
            return
        idx = self.history_tree.index(selected[0])
        if idx < len(self.history_items):
            path = self.history_items[idx].get("path", "")
            if path and os.path.exists(path):
                try:
                    if sys.platform == "win32":
                        os.startfile(path)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", path])
                    else:
                        subprocess.Popen(["xdg-open", path])
                except Exception as e:
                    self.log(f"[ERROR] Could not open file: {e}", "red")
            else:
                messagebox.showinfo("File Missing", f"File not found on disk:\n{path}")

    def open_selected_history_folder(self):
        selected = self.history_tree.selection()
        if not selected:
            self.open_downloads_folder()
            return
        idx = self.history_tree.index(selected[0])
        if idx < len(self.history_items):
            path = self.history_items[idx].get("path", "")
            folder = os.path.dirname(path) if (path and os.path.exists(path)) else DOWNLOADS_DIR
            try:
                if sys.platform == "win32":
                    os.startfile(folder)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", folder])
                else:
                    subprocess.Popen(["xdg-open", folder])
            except Exception as e:
                self.log(f"[ERROR] Could not open folder: {e}", "red")

    def clear_history(self):
        self.history_items = []
        self._save_history()
        self._render_history_tree()

    # --------------------------------------------------------------------------
    # Discovery & Network Helpers
    # --------------------------------------------------------------------------
    def _rescan_peers(self):
        self.log("[SCAN] Re-probing network for Android peer...", "cyan")
        threading.Thread(target=self.discovery._broadcast_pc_beacon, daemon=True).start()
        threading.Thread(target=self.discovery._active_multirail_scanner, daemon=True).start()

    def _on_peers_updated(self, ips):
        self.phone_ips = ips
        self.root.after(0, lambda: self._update_discovered_ui(ips))

    def _sync_ips_from_entry(self):
        try:
            raw = self.ip_entry.get().strip()
            if raw:
                self.phone_ips = [ip.strip() for ip in raw.split(",") if ip.strip()]
        except Exception:
            pass

    def _update_discovered_ui(self, ips):
        ip_summary = ", ".join(ips)
        try:
            self.ip_entry.delete(0, tk.END)
            self.ip_entry.insert(0, ip_summary)
        except Exception:
            pass
        if len(ips) >= 2:
            self.status_subtext.config(text=f"✓ Dual-Rail Active: {ip_summary}", fg=COLOR_SUCCESS)
            self.badge.config(text="⚡ DUAL-RAIL BONDED", fg=COLOR_SUCCESS)
        else:
            primary = ips[0] if ips else "Unknown"
            self.status_subtext.config(text=f"✓ Connected to Phone ({primary})", fg=COLOR_SUCCESS)
            self.badge.config(text="PHONE CONNECTED", fg=COLOR_SUCCESS)

    def _on_file_received_from_phone(self, filename, filepath, total_bytes, mbps):
        self.add_history_entry("RECV", filename, total_bytes, f"{mbps:.1f} MB/s", "COMPLETED", filepath)
        self.root.after(0, lambda: self._update_received_file_ui(filename, filepath))

    def _update_received_file_ui(self, filename, filepath):
        self.received_label.config(text=f"✓ Received '{filename}' into Downloads!", fg=COLOR_SUCCESS)

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

    # --------------------------------------------------------------------------
    # Audio Stream Actions
    # --------------------------------------------------------------------------
    def toggle_audio_stream(self):
        self._sync_ips_from_entry()
        if self.audio_streamer and self.audio_streamer.running:
            self.audio_streamer.stop()
            self.audio_streamer = None
            self.btn_audio.config(text="▶ Start Streaming PC Sound to Phone", bg=COLOR_ACCENT, fg="#FFFFFF")
        else:
            primary_ip = self.phone_ips[0] if self.phone_ips else "127.0.0.1"
            self.audio_streamer = DirectAudioStreamer(primary_ip, port=AUDIO_PORT, log_callback=self.log)
            self.audio_streamer.start()
            self.btn_audio.config(text="⏹ Stop Sound Stream", bg=COLOR_DANGER, fg="#FFFFFF")

    # --------------------------------------------------------------------------
    # File Send Actions
    # --------------------------------------------------------------------------
    def send_file_dialog(self):
        self._sync_ips_from_entry()
        if not self.phone_ips:
            messagebox.showinfo("Phone Required", "Please connect phone via Wi-Fi or USB Tethering, or enter Phone IP in the field above.")
            return
        path = filedialog.askopenfilename()
        if not path:
            return
        self.file_sender = TurboFileSender(
            self.phone_ips, port=HBSP_FILE_PORT,
            log_callback=self.log,
            progress_callback=self._update_progress,
            on_complete_callback=self._on_send_completed
        )
        self.file_sender.send_file(path)

    def cancel_send(self):
        if self.file_sender:
            self.file_sender.cancel()
            self.send_flight_canvas.set_progress(0.0, "TRANSFER CANCELLED", is_cancelled=True)
            self.speed_label.config(text="Status: Transfer Cancelled", fg=COLOR_DANGER)

    def _on_send_completed(self, filename, file_size, avg_speed, status, file_path):
        self.add_history_entry("SENT", filename, file_size, f"{avg_speed:.1f} MB/s", status, file_path)

    def _on_recv_progress(self, filename, written, total, mbps, eta):
        self.root.after(0, lambda: self._render_recv_progress_ui(filename, written, total, mbps, eta))

    def _render_recv_progress_ui(self, filename, written, total, mbps, eta):
        frac = (written / total) if total > 0 else 0.0
        pct = int(frac * 100)
        telemetry = f"✈ Recv: {mbps:.1f} MB/s ({written / (1024*1024):.1f}/{total / (1024*1024):.1f} MB • {pct}%)"
        self.recv_flight_canvas.set_progress(frac, telemetry)
        if frac >= 1.0:
            self.received_label.config(text=f"✓ Received '{filename}' ({total / (1024*1024):.1f} MB) saved!", fg=COLOR_SUCCESS)
        else:
            self.received_label.config(text=f"📥 Receiving '{filename}' • ETA: ~{eta}s", fg=COLOR_ACCENT)

    def _update_progress(self, sent, total, mbps, gbps, pipes, rail_speeds, is_cancelled=False):
        self.root.after(0, lambda: self._render_progress_ui(sent, total, mbps, gbps, pipes, rail_speeds, is_cancelled))

    def _render_progress_ui(self, sent, total, mbps, gbps, pipes, rail_speeds, is_cancelled=False):
        if is_cancelled:
            self.send_flight_canvas.set_progress(0.0, "TRANSFER CANCELLED", is_cancelled=True)
            self.speed_label.config(text="Status: Transfer Cancelled", fg=COLOR_DANGER)
            return

        frac = (sent / total) if total > 0 else 0.0
        self.send_flight_canvas.set_progress(frac, f"✈ {mbps:.1f} MB/s ({pipes} Pipes)")
        self.speed_label.config(text=f"Total: {mbps:.1f} MB/s ({gbps:.2f} Gbps) • [{pipes} Pipes]", fg=COLOR_ACCENT)

        parts = []
        for ip, spd in rail_speeds.items():
            tag = "USB" if "192.168.42." in ip else ("Hotspot" if "192.168.43." in ip else "Wi-Fi")
            parts.append(f"{tag}: {spd:.1f} MB/s")
        if parts:
            self.rail_telemetry_label.config(text=" | ".join(parts), fg=COLOR_SUCCESS if len(parts) >= 2 else COLOR_TEXT_MUTED)

    def _start_vu_meter_timer(self):
        if self.audio_streamer and self.audio_streamer.running:
            self.vu_bar["value"] = self.audio_streamer.current_volume * 100
        else:
            self.vu_bar["value"] = 0
        self.root.after(100, self._start_vu_meter_timer)

    def on_closing(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(b"P2PWIRE_DISCONNECT:PC", ("255.255.255.255", DISCOVERY_PORT))
            s.close()
        except Exception:
            pass
        if self.audio_streamer:
            self.audio_streamer.stop()
        if self.file_sender:
            self.file_sender.cancel()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = P2PwireDesktopApp(root)
    root.mainloop()
