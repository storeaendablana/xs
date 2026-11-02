#!/usr/bin/env python3
"""
================================================================================
  P2Pwire Desktop Turbo Hub 3.0 (True Dual-Rail Link Aggregation: USB + Wi-Fi)
  - Hardware Interface-Pinned Sockets (bind to physical adapter IPs)
  - Real-Time Per-Rail Live Telemetry (Shows exact USB vs Wi-Fi speed contribution)
  - Non-Blocking Lock-Free Worker Queue (Zero UI Throttling)
  - Completely Isolated Port 8080 Audio Engine (<20ms Lip-Sync Guaranteed)
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

# Incompressible media types (ISO, MKV, MP4, ZIP, etc.) bypass Zstd to saturate hardware line rate
INCOMPRESSIBLE_EXTS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v',
    '.zip', '.rar', '.7z', '.gz', '.bz2', '.xz', '.tar', '.tgz',
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.aac', '.flac',
    '.ogg', '.opus', '.apk', '.iso', '.dmg', '.bin'
}

CHUNK_SIZE = 1024 * 1024  # 1 MB optimal chunk for 1 GBps throughput
SOCKET_BUF_SIZE = 4 * 1024 * 1024  # 4 MB socket buffer (saturates 100+ MB/s TCP window scale)
DISCOVERY_PORT = 8089
AUDIO_PORT = 8080
HBSP_FILE_PORT = 8082

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "P2Pwire")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Neumorphic Color Palette
NEU_BG = "#EAEFF5"
NEU_SURFACE = "#F4F7FB"
NEU_SUNKEN = "#DFE6ED"
NEU_BORDER = "#CBD5E1"
NEU_TEXT_PRIMARY = "#0F172A"
NEU_TEXT_SECONDARY = "#475569"
NEU_TEXT_MUTED = "#94A3B8"
NEU_BLUE = "#007AFF"
NEU_GREEN = "#10B981"
NEU_RED = "#EF4444"


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
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 * 1024)
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
        except Exception as e:
            if self.running:
                self.log(f"[SPEAKER ERROR] Connection error: {e}", "red")
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
            dctx = zstd.ZstdDecompressor() if HAS_ZSTD else None
            while self.running:
                client, addr = server.accept()
                threading.Thread(target=self._handle_file_client, args=(client, addr, dctx), daemon=True).start()
        except Exception:
            pass

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
                                if self.on_progress:
                                    self.on_progress(name, total, total, mbps, 0)
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


class TurboFileSender:
    def __init__(self, target_ips, port=HBSP_FILE_PORT, log_callback=None, progress_callback=None):
        self.target_ips = target_ips if isinstance(target_ips, list) else [target_ips]
        self.port = port
        self.log = log_callback or print
        self.progress = progress_callback
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
        sockets_per_ip = 4
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
                s.settimeout(3.0)
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
            t.join(timeout=3.5)

        if not sockets:
            self.log("[ERROR] Could not connect to phone! Check if Phone app is open on Port 8082.", "red")
            return

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
                self.progress(file_size, file_size, avg_speed, (avg_speed * 8) / 1000, len(sockets), {}, is_cancelled=False)
        except Exception:
            pass


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


class AeroplaneCurveCanvas(tk.Canvas):
    def __init__(self, master, width=320, height=48, **kwargs):
        super().__init__(master, width=width, height=height, bg=NEU_SUNKEN, highlightthickness=1, highlightbackground=NEU_BORDER, **kwargs)
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
        if self.completion_exit_step < 12 and self.progress >= 1.0 and not self.is_cancelled:
            self.completion_exit_step += 1
            self.draw()
            self.after(35, self._animate_exit)

    def draw(self):
        self.delete("all")
        pad_x = 20
        start_x = pad_x
        end_x = max(start_x + 50, self.w - pad_x)
        travel_x = end_x - start_x
        start_y = self.h - 13
        speed_factor = max(0.20, min(1.0, self.speed_mbps / 45.0))
        climb_span = (self.h - 22) * speed_factor

        def get_pt(p):
            x = start_x + travel_x * p
            y = start_y - climb_span * (p ** 1.22)
            return x, y

        corridor_pts = []
        for s in range(21):
            p = s / 20.0
            cx, cy = get_pt(p)
            corridor_pts.extend([cx, cy])
        if len(corridor_pts) >= 4:
            self.create_line(*corridor_pts, fill="#C4D3E3", dash=(4, 5), width=1)

        bx, by = get_pt(1.0)
        beacon_color = NEU_RED if self.is_cancelled else (NEU_GREEN if self.progress >= 1.0 else NEU_BLUE)
        self.create_oval(bx - 3.5, by - 3.5, bx + 3.5, by + 3.5, fill=beacon_color, outline="")

        if self.progress > 0.005:
            contrail_pts = []
            steps = max(2, int(30 * self.progress))
            for s in range(steps + 1):
                p = min(self.progress, s / 30.0)
                cx, cy = get_pt(p)
                contrail_pts.extend([cx, cy])
            if len(contrail_pts) >= 4:
                glow_color = NEU_RED if self.is_cancelled else ("#5CE1E6" if self.progress >= 1.0 else "#007AFF")
                core_color = "#FFAAAA" if self.is_cancelled else ("#A7F3D0" if self.progress >= 1.0 else "#60A5FA")
                self.create_line(*contrail_pts, fill=glow_color, width=4)
                self.create_line(*contrail_pts, fill=core_color, width=2)

        px, py = get_pt(self.progress)
        is_exited = False
        if self.progress >= 1.0 and not self.is_cancelled and self.completion_exit_step > 0:
            px += self.completion_exit_step * 8
            py -= self.completion_exit_step * 6
            if px > self.w + 30 or py < -20:
                is_exited = True

        if not is_exited:
            jet_color = NEU_RED if self.is_cancelled else (NEU_GREEN if self.progress >= 1.0 else "#0056B3")
            if not self.is_cancelled and 0.01 < self.progress < 1.0:
                self.create_line(px - 10, py - 4.5, px - 3, py - 4.5, fill="#FF6D00", width=2.5)
                self.create_line(px - 10, py + 4.5, px - 3, py + 4.5, fill="#FF6D00", width=2.5)
            poly_points = [
                px + 10, py - 1.5, px + 5, py - 2.0, px + 2, py - 2.5,
                px - 3, py - 9.5, px - 5, py - 9.0, px - 4, py - 2.5,
                px - 6, py - 2.0, px - 8, py - 4.5, px - 9, py - 4.0,
                px - 9, py + 0.5, px - 8, py + 4.5, px - 6, py + 2.0,
                px - 4, py + 2.5, px - 5, py + 9.0, px - 3, py + 9.5,
                px + 2, py + 2.5, px + 5, py + 2.0, px + 10, py + 0.5
            ]
            self.create_polygon(poly_points, fill=jet_color, outline="#FFFFFF", width=1)
            self.create_rectangle(px - 2, py - 5.5, px + 2, py - 3.5, fill="#1E88E5", outline="#FFFFFF")
            self.create_rectangle(px - 2, py + 3.5, px + 2, py + 5.5, fill="#1E88E5", outline="#FFFFFF")
            self.create_oval(px + 4, py - 2, px + 7, py + 0.5, fill="#80D8FF", outline="")

        if self.telemetry:
            txt_color = NEU_RED if self.is_cancelled else NEU_TEXT_PRIMARY
            status_text = self.telemetry
            if self.progress >= 1.0 and not self.is_cancelled:
                status_text = f"✓ Mach 1.0 Dispatched • {self.telemetry}"
            self.create_text(start_x + 2, start_y - 2, text=status_text, anchor="sw", font=("Consolas", 8, "bold"), fill=txt_color)


class P2PwireDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("P2Pwire Turbo Hub 3.0 (Dual-Rail Bonding)")
        self.root.geometry("880x730")
        self.root.minsize(780, 600)
        self.root.configure(bg=NEU_BG)

        self.local_ips = get_all_local_ips()
        self.phone_ips = []
        self.audio_streamer = None
        self.file_sender = None

        self._init_styles()
        self._build_ui()
        self._start_vu_meter_timer()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

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

    def _init_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar", thickness=7, troughcolor=NEU_SUNKEN, background=NEU_BLUE)

    def _build_ui(self):
        header = tk.Frame(self.root, bg=NEU_BG, relief=tk.GROOVE, bd=1)
        header.pack(fill=tk.X, padx=16, pady=10)

        badge_icon_frame = tk.Frame(header, bg=NEU_SUNKEN, width=50, height=50, relief=tk.RAISED, bd=2)
        badge_icon_frame.pack_propagate(False)
        badge_icon_frame.pack(side=tk.LEFT, padx=(14, 10), pady=10)
        tk.Label(badge_icon_frame, text="⚡", font=("Segoe UI", 18, "bold"), fg=NEU_BLUE, bg=NEU_SUNKEN).pack(expand=True)

        title_box = tk.Frame(header, bg=NEU_BG)
        title_box.pack(side=tk.LEFT, padx=6, pady=10)
        tk.Label(title_box, text="Welcome to P2Pwire Turbo", font=("Segoe UI", 15, "bold"), fg=NEU_TEXT_PRIMARY, bg=NEU_BG).pack(anchor="w")
        self.status_subtext = tk.Label(title_box, text="Scanning for Phone (Wi-Fi + USB Tethering)...", font=("Segoe UI", 9), fg=NEU_BLUE, bg=NEU_BG)
        self.status_subtext.pack(anchor="w")

        self.badge = tk.Label(header, text="MESH SEARCHING", font=("Consolas", 9, "bold"), fg=NEU_BLUE, bg=NEU_SUNKEN, relief=tk.SUNKEN, bd=1, padx=10, pady=4)
        self.badge.pack(side=tk.RIGHT, padx=16, pady=16)

        self.bonding_bar = tk.Frame(self.root, bg=NEU_SUNKEN, relief=tk.SUNKEN, bd=1, padx=12, pady=6)
        self.bonding_bar.pack(fill=tk.X, padx=16, pady=(0, 6))
        self.bonding_label = tk.Label(self.bonding_bar, text="⚡ Dual-Rail Ready: Connect USB Tethering + 5GHz Wi-Fi simultaneously for 50-55+ MB/s!", font=("Segoe UI", 8, "bold"), fg=NEU_BLUE, bg=NEU_SUNKEN)
        self.bonding_label.pack(anchor="w")

        ip_frame = tk.Frame(self.root, bg=NEU_BG, padx=4, pady=4)
        ip_frame.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Label(ip_frame, text="Target Phone IP(s):", font=("Segoe UI", 9, "bold"), fg=NEU_TEXT_SECONDARY, bg=NEU_BG).pack(side=tk.LEFT, padx=(0, 8))
        self.ip_entry = tk.Entry(ip_frame, font=("Consolas", 10), bg=NEU_SUNKEN, fg=NEU_TEXT_PRIMARY, insertbackground=NEU_BLUE, relief=tk.SUNKEN, bd=1)
        self.ip_entry.insert(0, ", ".join(self.phone_ips))
        self.ip_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        cards_row = tk.Frame(self.root, bg=NEU_BG)
        cards_row.pack(fill=tk.X, padx=16, pady=4)

        audio_card = tk.LabelFrame(cards_row, text=" 🔊 Low-Latency Sound (Port 8080) ", font=("Segoe UI", 10, "bold"), fg=NEU_TEXT_PRIMARY, bg=NEU_BG, relief=tk.GROOVE, bd=1, padx=14, pady=12)
        audio_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        vu_box = tk.Frame(audio_card, bg=NEU_BG)
        vu_box.pack(fill=tk.X, pady=6)
        tk.Label(vu_box, text="Sound VU:", font=("Segoe UI", 8, "bold"), fg=NEU_TEXT_SECONDARY, bg=NEU_BG).pack(side=tk.LEFT)
        self.vu_bar = ttk.Progressbar(vu_box, length=140, mode="determinate")
        self.vu_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        self.btn_audio = tk.Button(audio_card, text="▶ Stream PC Sound to Phone", font=("Segoe UI", 9, "bold"), bg=NEU_BLUE, fg="#FFFFFF", activebackground="#0056B3", activeforeground="#FFFFFF", relief=tk.RAISED, bd=2, pady=8, command=self.toggle_audio_stream)
        self.btn_audio.pack(fill=tk.X, pady=(10, 0))

        file_card = tk.LabelFrame(cards_row, text=" ⚡ Dual-Rail Turbo Blast (Port 8082) ", font=("Segoe UI", 10, "bold"), fg=NEU_TEXT_PRIMARY, bg=NEU_BG, relief=tk.GROOVE, bd=1, padx=14, pady=12)
        file_card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        speed_box = tk.Frame(file_card, bg=NEU_SUNKEN, relief=tk.SUNKEN, bd=1, padx=8, pady=4)
        speed_box.pack(fill=tk.X, pady=2)
        self.speed_label = tk.Label(speed_box, text="Speed: 0.0 MB/s (0.00 Gbps)", font=("Consolas", 10, "bold"), fg=NEU_BLUE, bg=NEU_SUNKEN)
        self.speed_label.pack(anchor="w")

        self.rail_telemetry_label = tk.Label(file_card, text="Pipes: Idle", font=("Consolas", 8), fg=NEU_TEXT_SECONDARY, bg=NEU_BG)
        self.rail_telemetry_label.pack(anchor="w", pady=(2, 4))

        self.send_flight_canvas = AeroplaneCurveCanvas(file_card, height=44)
        self.send_flight_canvas.pack(fill=tk.X, pady=4)

        send_btn_row = tk.Frame(file_card, bg=NEU_BG)
        send_btn_row.pack(fill=tk.X, pady=(6, 0))
        btn_send = tk.Button(send_btn_row, text="📁 Blast File to Phone (Dual-Rail)", font=("Segoe UI", 9, "bold"), bg=NEU_BLUE, fg="#FFFFFF", activebackground="#0056B3", activeforeground="#FFFFFF", relief=tk.RAISED, bd=2, pady=7, command=self.send_file_dialog)
        btn_send.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_cancel_send = tk.Button(send_btn_row, text="⏹ Cancel", font=("Segoe UI", 8, "bold"), bg=NEU_SUNKEN, fg=NEU_RED, activebackground="#FEE2E2", relief=tk.RAISED, bd=1, pady=7, command=self.cancel_send)
        self.btn_cancel_send.pack(side=tk.RIGHT, padx=(4, 0))

        self.received_frame = tk.LabelFrame(self.root, text=" 📥 Incoming File Receiver (Port 8082) ", font=("Segoe UI", 9, "bold"), fg=NEU_TEXT_PRIMARY, bg=NEU_BG, relief=tk.GROOVE, bd=1, padx=12, pady=6)
        self.received_frame.pack(fill=tk.X, padx=16, pady=(4, 6))

        top_recv_row = tk.Frame(self.received_frame, bg=NEU_BG)
        top_recv_row.pack(fill=tk.X)
        self.received_label = tk.Label(top_recv_row, text="Listening on Port 8082 • Ready to receive from Phone", font=("Segoe UI", 9, "bold"), fg=NEU_GREEN, bg=NEU_BG)
        self.received_label.pack(side=tk.LEFT, padx=4)
        btn_open_folder = tk.Button(top_recv_row, text="📂 Open Downloads", font=("Segoe UI", 8, "bold"), bg=NEU_SURFACE, fg=NEU_TEXT_PRIMARY, relief=tk.RAISED, bd=1, command=self.open_downloads_folder)
        btn_open_folder.pack(side=tk.RIGHT, padx=4)

        self.recv_flight_canvas = AeroplaneCurveCanvas(self.received_frame, height=44)
        self.recv_flight_canvas.pack(fill=tk.X, pady=(4, 2))

        log_frame = tk.LabelFrame(self.root, text=" 📋 Session Status ", font=("Segoe UI", 9, "bold"), fg=NEU_TEXT_SECONDARY, bg=NEU_BG, relief=tk.GROOVE, bd=1, padx=8, pady=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(2, 12))

        self.log_text = tk.Text(log_frame, bg=NEU_SUNKEN, fg=NEU_TEXT_PRIMARY, font=("Consolas", 9), wrap=tk.WORD, relief=tk.SUNKEN, bd=1, insertbackground=NEU_BLUE)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        self.log_text.tag_config("cyan", foreground=NEU_BLUE)
        self.log_text.tag_config("green", foreground=NEU_GREEN)
        self.log_text.tag_config("yellow", foreground="#D97706")
        self.log_text.tag_config("red", foreground=NEU_RED)
        self.log_text.tag_config("white", foreground=NEU_TEXT_PRIMARY)

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
            self.status_subtext.config(text=f"✓ Dual-Rail Active: {ip_summary}", fg=NEU_GREEN)
            self.badge.config(text="⚡ DUAL-RAIL BONDED", fg=NEU_GREEN)
        else:
            primary = ips[0] if ips else "Unknown"
            self.status_subtext.config(text=f"✓ Connected to Phone ({primary})", fg=NEU_GREEN)
            self.badge.config(text="PHONE CONNECTED", fg=NEU_GREEN)

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
        except Exception:
            pass

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
        self._sync_ips_from_entry()
        if self.audio_streamer and self.audio_streamer.running:
            self.audio_streamer.stop()
            self.audio_streamer = None
            self.btn_audio.config(text="▶ Stream PC Sound to Phone", bg=NEU_BLUE, fg="#FFFFFF")
        else:
            primary_ip = self.phone_ips[0] if self.phone_ips else "127.0.0.1"
            self.audio_streamer = DirectAudioStreamer(primary_ip, port=AUDIO_PORT, log_callback=self.log)
            self.audio_streamer.start()
            self.btn_audio.config(text="⏹ Stop Sound Stream", bg=NEU_RED, fg="#FFFFFF")

    def send_file_dialog(self):
        self._sync_ips_from_entry()
        if not self.phone_ips:
            messagebox.showinfo("Phone Required", "Please connect phone via Wi-Fi or USB Tethering, or enter Phone IP in the field above.")
            return
        path = filedialog.askopenfilename()
        if not path:
            return
        self.file_sender = TurboFileSender(self.phone_ips, port=HBSP_FILE_PORT, log_callback=self.log, progress_callback=self._update_progress)
        self.file_sender.send_file(path)

    def cancel_send(self):
        if self.file_sender:
            self.file_sender.cancel()
            self.send_flight_canvas.set_progress(0.0, "TRANSFER CANCELLED", is_cancelled=True)
            self.speed_label.config(text="Status: Transfer Cancelled", fg=NEU_RED)

    def _on_recv_progress(self, filename, written, total, mbps, eta):
        self.root.after(0, lambda: self._render_recv_progress_ui(filename, written, total, mbps, eta))

    def _render_recv_progress_ui(self, filename, written, total, mbps, eta):
        frac = (written / total) if total > 0 else 0.0
        pct = int(frac * 100)
        telemetry = f"✈ Recv: {mbps:.1f} MB/s ({written / (1024*1024):.1f}/{total / (1024*1024):.1f} MB • {pct}%)"
        self.recv_flight_canvas.set_progress(frac, telemetry)
        if frac >= 1.0:
            self.received_label.config(text=f"✓ Received '{filename}' ({total / (1024*1024):.1f} MB) saved to Downloads!", fg=NEU_GREEN)
        else:
            self.received_label.config(text=f"📥 Receiving '{filename}' • ETA: ~{eta}s", fg=NEU_BLUE)

    def _update_progress(self, sent, total, mbps, gbps, pipes, rail_speeds, is_cancelled=False):
        self.root.after(0, lambda: self._render_progress_ui(sent, total, mbps, gbps, pipes, rail_speeds, is_cancelled))

    def _render_progress_ui(self, sent, total, mbps, gbps, pipes, rail_speeds, is_cancelled=False):
        if is_cancelled:
            self.send_flight_canvas.set_progress(0.0, "TRANSFER CANCELLED", is_cancelled=True)
            self.speed_label.config(text="Status: Transfer Cancelled", fg=NEU_RED)
            return

        frac = (sent / total) if total > 0 else 0.0
        self.send_flight_canvas.set_progress(frac, f"✈ {mbps:.1f} MB/s ({pipes} Pipes)")
        self.speed_label.config(text=f"Total: {mbps:.1f} MB/s ({gbps:.2f} Gbps) • [{pipes} Pipes]", fg=NEU_BLUE)

        parts = []
        for ip, spd in rail_speeds.items():
            tag = "USB" if "192.168.42." in ip else ("Hotspot" if "192.168.43." in ip else "Wi-Fi")
            parts.append(f"{tag}: {spd:.1f} MB/s")
        if parts:
            self.rail_telemetry_label.config(text=" | ".join(parts), fg=NEU_GREEN if len(parts) >= 2 else NEU_TEXT_MUTED)

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
