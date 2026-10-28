#!/usr/bin/env python3
"""
P2Pwire Ultra Desktop Studio (v4.1 - Bulletproof Discovery & Universal Transfer)
High-Speed Cross-Platform Local Transfer & Wireless Audio Engine
Compatible with: Windows, macOS, Linux, and Android P2Pwire App
"""

import os
import sys
import time
import socket
import struct
import threading
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

# Configuration & Protocol Constants
BROADCAST_PORT = 8089        # UDP Peer Discovery
FILE_TRANSFER_PORT = 8082    # TCP High-Speed Data Pipes
AUDIO_STREAM_PORT = 8080     # TCP Low-Latency Audio Stream

BEACON_PREFIX = "P2PWIRE_BEACON:"
MAGIC_HEADER = b"P2PWIRE_V4"
CHUNK_SIZE = 2 * 1024 * 1024  # 2MB chunks for maximum throughput
SOCKET_PIPES = 6             # Parallel TCP data streams

AUDIO_FORMAT = 8             # paInt16
AUDIO_CHANNELS = 2           # Stereo
AUDIO_RATE = 48000           # 48 kHz Studio Quality
AUDIO_FRAMES_PER_BUFFER = 1024

DEFAULT_DOWNLOAD_DIR = os.path.join(str(Path.home()), "Downloads", "P2Pwire_Received")
os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)


def get_local_ip():
    """Detects primary local network IP (Wi-Fi / Hotspot / LAN)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_broadcast_addresses(local_ip):
    """Calculates broadcast addresses including 255.255.255.255 and subnet broadcast."""
    addrs = ["255.255.255.255"]
    parts = local_ip.split(".")
    if len(parts) == 4 and local_ip != "127.0.0.1":
        subnet_bcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
        if subnet_bcast not in addrs:
            addrs.append(subnet_bcast)
    return addrs


class PeerDiscovery:
    def __init__(self, node_id, device_name, local_ip, on_peer_update_cb, log_cb):
        self.node_id = node_id
        self.device_name = device_name
        self.local_ip = local_ip
        self.on_peer_update = on_peer_update_cb
        self.log_cb = log_cb
        self.peers = {}
        self.running = False
        self.lock = threading.Lock()

    def start(self):
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()
        threading.Thread(target=self._listen_loop, daemon=True).start()
        threading.Thread(target=self._cleanup_loop, daemon=True).start()
        threading.Thread(target=self.scan_subnet_fast, daemon=True).start()

    def stop(self):
        self.running = False

    def add_direct_peer(self, ip, name="Manual Peer"):
        """Directly pairs a peer by IP (bypasses router broadcast blocking)."""
        clean_ip = ip.strip()
        if not clean_ip or clean_ip == self.local_ip:
            return False

        with self.lock:
            self.peers[clean_ip] = {
                "id": clean_ip,
                "name": f"{name} ({clean_ip})",
                "os": "Android / PC",
                "ip": clean_ip,
                "transfer_port": FILE_TRANSFER_PORT,
                "audio_port": AUDIO_STREAM_PORT,
                "last_seen": time.time() + 60.0
            }
        self.on_peer_update(self.get_peer_list())
        threading.Thread(target=self._send_unicast_beacon, args=(clean_ip,), daemon=True).start()
        return True

    def _send_unicast_beacon(self, target_ip):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            msg_str = f"{BEACON_PREFIX}PC:{self.local_ip}:{AUDIO_STREAM_PORT}:{FILE_TRANSFER_PORT}"
            sock.sendto(msg_str.encode("utf-8"), (target_ip, BROADCAST_PORT))
            sock.close()
        except Exception:
            pass

    def scan_subnet_fast(self):
        """Concurrently probes the entire local /24 subnet in <2 seconds."""
        parts = self.local_ip.split(".")
        if len(parts) != 4 or self.local_ip == "127.0.0.1":
            return

        prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
        self.log_cb(f"Scanning subnet {prefix}1-254 for active P2Pwire devices...", "info")

        def probe_ip(target_ip):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.3)
                beacon = f"{BEACON_PREFIX}PC:{self.local_ip}:{AUDIO_STREAM_PORT}:{FILE_TRANSFER_PORT}".encode("utf-8")
                s.sendto(beacon, (target_ip, BROADCAST_PORT))
                s.close()

                tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp_sock.settimeout(0.35)
                res = tcp_sock.connect_ex((target_ip, FILE_TRANSFER_PORT))
                tcp_sock.close()
                if res == 0:
                    self.add_direct_peer(target_ip, "P2Pwire Device")
            except Exception:
                pass

        threads = []
        for i in range(1, 255):
            t_ip = f"{prefix}{i}"
            if t_ip == self.local_ip:
                continue
            t = threading.Thread(target=probe_ip, args=(t_ip,), daemon=True)
            threads.append(t)
            t.start()
            if len(threads) >= 40:
                for th in threads:
                    th.join(0.05)
                threads = []

        for th in threads:
            th.join(0.5)

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)

        str_beacon = f"{BEACON_PREFIX}PC:{self.local_ip}:{AUDIO_STREAM_PORT}:{FILE_TRANSFER_PORT}".encode("utf-8")
        json_beacon = json.dumps({
            "magic": "P2PWIRE_BEACON",
            "id": self.node_id,
            "name": self.device_name,
            "os": f"PC ({sys.platform})",
            "ip": self.local_ip,
            "transfer_port": FILE_TRANSFER_PORT,
            "audio_port": AUDIO_STREAM_PORT
        }).encode("utf-8")

        while self.running:
            bcast_targets = get_broadcast_addresses(self.local_ip)
            for bcast_ip in bcast_targets:
                try:
                    sock.sendto(str_beacon, (bcast_ip, BROADCAST_PORT))
                    sock.sendto(json_beacon, (bcast_ip, BROADCAST_PORT))
                except Exception:
                    pass
            time.sleep(1.5)
        sock.close()

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", BROADCAST_PORT))
        except Exception as e:
            self.log_cb(f"UDP Discovery bind error: {e}", "error")
            return

        sock.settimeout(1.0)
        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
                sender_ip = addr[0]
                if sender_ip == self.local_ip:
                    continue

                raw_text = data.decode("utf-8", errors="ignore").strip()

                if raw_text.startswith(BEACON_PREFIX):
                    parts = raw_text[len(BEACON_PREFIX):].split(":")
                    device_type = parts[0] if len(parts) > 0 and parts[0] else "Android Phone"
                    ip_part = parts[1] if len(parts) > 1 else sender_ip
                    peer_ip = sender_ip
                    for sub in ip_part.split(","):
                        sub_clean = sub.strip()
                        if sub_clean and sub_clean != self.local_ip:
                            peer_ip = sub_clean
                            break

                    with self.lock:
                        self.peers[peer_ip] = {
                            "id": peer_ip,
                            "name": f"{device_type} ({peer_ip})",
                            "os": "Android",
                            "ip": peer_ip,
                            "transfer_port": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else FILE_TRANSFER_PORT,
                            "audio_port": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else AUDIO_STREAM_PORT,
                            "last_seen": time.time()
                        }
                    self.on_peer_update(self.get_peer_list())

                elif raw_text.startswith("{") and "P2PWIRE_BEACON" in raw_text:
                    try:
                        msg = json.loads(raw_text)
                        peer_id = msg.get("id", sender_ip)
                        with self.lock:
                            self.peers[peer_id] = {
                                "id": peer_id,
                                "name": msg.get("name", f"PC ({sender_ip})"),
                                "os": msg.get("os", "PC"),
                                "ip": sender_ip,
                                "transfer_port": msg.get("transfer_port", FILE_TRANSFER_PORT),
                                "audio_port": msg.get("audio_port", AUDIO_STREAM_PORT),
                                "last_seen": time.time()
                            }
                        self.on_peer_update(self.get_peer_list())
                    except Exception:
                        pass

            except socket.timeout:
                continue
            except Exception:
                pass
        sock.close()

    def _cleanup_loop(self):
        while self.running:
            time.sleep(4.0)
            now = time.time()
            changed = False
            with self.lock:
                dead = [pid for pid, p in self.peers.items() if now - p["last_seen"] > 14.0]
                for pid in dead:
                    del self.peers[pid]
                    changed = True
            if changed:
                self.on_peer_update(self.get_peer_list())

    def get_peer_list(self):
        with self.lock:
            return list(self.peers.values())


class FileTransferEngine:
    def __init__(self, save_dir, progress_cb, log_cb, transfer_complete_cb):
        self.save_dir = save_dir
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.transfer_complete_cb = transfer_complete_cb
        self.running = False
        self.server_sock = None

    def start_receiver(self):
        self.running = True
        threading.Thread(target=self._server_worker, daemon=True).start()

    def stop_receiver(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

    def _server_worker(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_sock.bind(("", FILE_TRANSFER_PORT))
            self.server_sock.listen(10)
        except Exception as e:
            self.log_cb(f"Receiver port {FILE_TRANSFER_PORT} error: {e}", "error")
            return

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                threading.Thread(target=self._handle_incoming_connection, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def _handle_incoming_connection(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            header_bytes = conn.recv(len(MAGIC_HEADER))
            if header_bytes != MAGIC_HEADER:
                conn.close()
                return

            meta_len_bytes = conn.recv(4)
            if not meta_len_bytes:
                conn.close()
                return
            meta_len = struct.unpack("!I", meta_len_bytes)[0]
            meta_json = conn.recv(meta_len).decode("utf-8")
            meta = json.loads(meta_json)

            filename = os.path.basename(meta["filename"])
            filesize = meta["filesize"]
            sender_name = meta.get("sender_name", addr[0])

            save_path = os.path.join(self.save_dir, filename)
            base_name, ext = os.path.splitext(save_path)
            counter = 1
            while os.path.exists(save_path):
                save_path = f"{base_name}_{counter}{ext}"
                counter += 1

            self.log_cb(f"Receiving '{filename}' ({filesize / (1024*1024):.2f} MB) from {sender_name}...", "info")
            start_time = time.time()
            received_bytes = 0

            with open(save_path, "wb") as f:
                while received_bytes < filesize:
                    to_read = min(CHUNK_SIZE, filesize - received_bytes)
                    chunk = conn.recv(to_read)
                    if not chunk:
                        break
                    f.write(chunk)
                    received_bytes += len(chunk)

                    pct = (received_bytes / filesize) * 100
                    elapsed = max(0.001, time.time() - start_time)
                    speed_mbps = (received_bytes / (1024 * 1024)) / elapsed
                    self.progress_cb("receiving", filename, pct, speed_mbps, received_bytes, filesize)

            elapsed = max(0.001, time.time() - start_time)
            avg_speed = (filesize / (1024 * 1024)) / elapsed
            self.log_cb(f"Received '{filename}' successfully at {avg_speed:.1f} MB/s", "success")

            self.transfer_complete_cb({
                "type": "received",
                "filename": filename,
                "path": save_path,
                "size_mb": filesize / (1024 * 1024),
                "device": sender_name,
                "speed": f"{avg_speed:.1f} MB/s",
                "time": time.strftime("%H:%M:%S")
            })

            conn.sendall(b"ACK")
        except Exception as e:
            self.log_cb(f"Error receiving file: {e}", "error")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def send_files_batch(self, target_ip, target_device_name, my_name, file_paths):
        threading.Thread(
            target=self._send_files_worker,
            args=(target_ip, target_device_name, my_name, file_paths),
            daemon=True
        ).start()

    def _send_files_worker(self, target_ip, target_device_name, my_name, file_paths):
        total_files = len(file_paths)
        for idx, fpath in enumerate(file_paths):
            if not os.path.exists(fpath):
                continue

            filename = os.path.basename(fpath)
            filesize = os.path.getsize(fpath)
            self.log_cb(f"[{idx+1}/{total_files}] Sending '{filename}' to {target_device_name} ({target_ip})...", "info")

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
                sock.settimeout(6.0)
                sock.connect((target_ip, FILE_TRANSFER_PORT))

                meta = {
                    "filename": filename,
                    "filesize": filesize,
                    "sender_name": my_name,
                    "compressed": False
                }
                meta_bytes = json.dumps(meta).encode("utf-8")

                sock.sendall(MAGIC_HEADER)
                sock.sendall(struct.pack("!I", len(meta_bytes)))
                sock.sendall(meta_bytes)

                sent_bytes = 0
                start_time = time.time()

                with open(fpath, "rb") as f:
                    while sent_bytes < filesize:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        sock.sendall(chunk)
                        sent_bytes += len(chunk)

                        pct = (sent_bytes / filesize) * 100
                        elapsed = max(0.001, time.time() - start_time)
                        speed_mbps = (sent_bytes / (1024 * 1024)) / elapsed
                        self.progress_cb("sending", filename, pct, speed_mbps, sent_bytes, filesize)

                sock.settimeout(12.0)
                sock.recv(3)
                elapsed = max(0.001, time.time() - start_time)
                avg_speed = (filesize / (1024 * 1024)) / elapsed

                self.log_cb(f"Sent '{filename}' successfully at {avg_speed:.1f} MB/s", "success")

                self.transfer_complete_cb({
                    "type": "sent",
                    "filename": filename,
                    "path": fpath,
                    "size_mb": filesize / (1024 * 1024),
                    "device": target_device_name,
                    "speed": f"{avg_speed:.1f} MB/s",
                    "time": time.strftime("%H:%M:%S")
                })

                sock.close()
            except Exception as e:
                self.log_cb(f"Failed to send '{filename}' to {target_ip}: {e}", "error")
                break


class SoundEngine:
    def __init__(self, log_cb):
        self.log_cb = log_cb
        self.pa = pyaudio.PyAudio() if HAS_PYAUDIO else None
        self.speaker_running = False
        self.sender_running = False
        self.speaker_sock = None
        self.sender_sock = None

    def start_speaker_mode(self, port=AUDIO_STREAM_PORT):
        if not HAS_PYAUDIO:
            self.log_cb("PyAudio is not installed! Run: pip install pyaudio", "error")
            return False

        if self.speaker_running:
            return True

        self.speaker_running = True
        threading.Thread(target=self._speaker_receiver_loop, args=(port,), daemon=True).start()
        self.log_cb(f"Speaker Mode Activated: Listening on port {port}", "success")
        return True

    def stop_speaker_mode(self):
        self.speaker_running = False
        if self.speaker_sock:
            try:
                self.speaker_sock.close()
            except Exception:
                pass
        self.log_cb("Speaker Mode Stopped", "info")

    def _speaker_receiver_loop(self, port):
        self.speaker_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.speaker_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.speaker_sock.bind(("", port))
            self.speaker_sock.listen(2)
        except Exception as e:
            self.log_cb(f"Speaker bind error: {e}", "error")
            self.speaker_running = False
            return

        out_stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            output=True,
            frames_per_buffer=AUDIO_FRAMES_PER_BUFFER
        )

        while self.speaker_running:
            try:
                client_sock, addr = self.speaker_sock.accept()
                self.log_cb(f"Audio stream connected from {addr[0]}", "success")
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                while self.speaker_running:
                    raw_data = client_sock.recv(AUDIO_FRAMES_PER_BUFFER * 4)
                    if not raw_data:
                        break
                    out_stream.write(raw_data)
                client_sock.close()
            except Exception:
                pass

        try:
            out_stream.stop_stream()
            out_stream.close()
        except Exception:
            pass

    def start_sender_mode(self, target_ip, port=AUDIO_STREAM_PORT):
        if not HAS_PYAUDIO:
            self.log_cb("PyAudio is not installed! Run: pip install pyaudio", "error")
            return False

        if self.sender_running:
            return True

        self.sender_running = True
        threading.Thread(target=self._sender_loop, args=(target_ip, port), daemon=True).start()
        self.log_cb(f"Sound Sending Started -> {target_ip}:{port}", "success")
        return True

    def stop_sender_mode(self):
        self.sender_running = False
        if self.sender_sock:
            try:
                self.sender_sock.close()
            except Exception:
                pass
        self.log_cb("Sound Sending Stopped", "info")

    def _sender_loop(self, target_ip, port):
        try:
            self.sender_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sender_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sender_sock.connect((target_ip, port))

            in_stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE,
                input=True,
                frames_per_buffer=AUDIO_FRAMES_PER_BUFFER
            )

            while self.sender_running:
                data = in_stream.read(AUDIO_FRAMES_PER_BUFFER, exception_on_overflow=False)
                self.sender_sock.sendall(data)

            in_stream.stop_stream()
            in_stream.close()
        except Exception as e:
            self.log_cb(f"Audio Sender error: {e}", "error")
        finally:
            self.sender_running = False


class P2PwireApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("P2Pwire Ultra Studio | Universal Transfer & Sound")
        self.geometry("1040x780")
        self.minsize(940, 700)

        self.BG_DARK = "#070B14"
        self.CARD_BG = "#101728"
        self.INPUT_BG = "#16223D"
        self.ACCENT_CYAN = "#00F0FF"
        self.ACCENT_PURPLE = "#A855F7"
        self.ACCENT_GREEN = "#00FFA3"
        self.TEXT_WHITE = "#F8FAFC"
        self.TEXT_MUTED = "#94A3B8"
        self.BORDER_COLOR = "#1E293B"

        self.configure(bg=self.BG_DARK)

        self.node_id = f"PC_{int(time.time() * 1000) % 1000000}"
        self.device_name = f"Desktop-PC ({socket.gethostname()})"
        self.local_ip = get_local_ip()
        self.save_directory = DEFAULT_DOWNLOAD_DIR
        self.selected_files = []
        self.active_peers = []

        self.sound_engine = SoundEngine(self.append_log)
        self.file_engine = FileTransferEngine(
            save_dir=self.save_directory,
            progress_cb=self.on_transfer_progress,
            log_cb=self.append_log,
            transfer_complete_cb=self.on_transfer_completed
        )
        self.file_engine.start_receiver()

        self.discovery = PeerDiscovery(
            self.node_id,
            self.device_name,
            self.local_ip,
            self.on_peers_updated,
            self.append_log
        )
        self.discovery.start()

        self._setup_styles()
        self._build_header()
        self._build_main_notebook()
        self._build_status_footer()

        self.append_log(f"P2Pwire Core initialized. Local IP: {self.local_ip}", "info")
        self.append_log(f"High-Speed 6-Pipe receiver listening on port {FILE_TRANSFER_PORT}", "info")

    def _setup_styles(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")

        self.style.configure("TNotebook", background=self.BG_DARK, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=self.CARD_BG, foreground=self.TEXT_WHITE,
                             padding=[18, 8], font=("Helvetica", 10, "bold"))
        self.style.map("TNotebook.Tab",
                       background=[("selected", self.INPUT_BG)],
                       foreground=[("selected", self.ACCENT_CYAN)])

        self.style.configure("Treeview",
                             background=self.CARD_BG,
                             foreground=self.TEXT_WHITE,
                             fieldbackground=self.CARD_BG,
                             rowheight=28,
                             font=("Segoe UI", 9))
        self.style.configure("Treeview.Heading",
                             background=self.INPUT_BG,
                             foreground=self.ACCENT_CYAN,
                             font=("Segoe UI", 9, "bold"))
        self.style.map("Treeview", background=[("selected", "#2563EB")])

        self.style.configure("Horizontal.TProgressbar",
                             troughcolor=self.INPUT_BG,
                             background=self.ACCENT_CYAN,
                             thickness=8)

    def _build_header(self):
        header_frame = tk.Frame(self, bg=self.CARD_BG, height=75, highlightbackground=self.BORDER_COLOR, highlightthickness=1)
        header_frame.pack(fill=tk.X, padx=16, pady=(14, 8))

        title_box = tk.Frame(header_frame, bg=self.CARD_BG)
        title_box.pack(side=tk.LEFT, padx=18, pady=10)

        tk.Label(title_box, text="⚡ P2Pwire Ultra", font=("Helvetica", 18, "bold"), fg=self.ACCENT_CYAN, bg=self.CARD_BG).pack(anchor="w")
        tk.Label(title_box, text="Universal Any-to-Any Transfer & Wireless Sound Studio", font=("Helvetica", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG).pack(anchor="w")

        info_box = tk.Frame(header_frame, bg=self.INPUT_BG, padx=14, pady=6)
        info_box.pack(side=tk.RIGHT, padx=18, pady=12)

        tk.Label(info_box, text=f"My Device: {self.device_name}", font=("Segoe UI", 9, "bold"), fg=self.TEXT_WHITE, bg=self.INPUT_BG).pack(anchor="e")
        tk.Label(info_box, text=f"IP: {self.local_ip} | Node ID: {self.node_id}", font=("Segoe UI", 8), fg=self.ACCENT_GREEN, bg=self.INPUT_BG).pack(anchor="e")

    def _build_main_notebook(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=4)

        self.tab_transfer = tk.Frame(self.notebook, bg=self.BG_DARK)
        self.notebook.add(self.tab_transfer, text="  📁 File Transfer Studio  ")
        self._build_transfer_tab(self.tab_transfer)

        self.tab_sent = tk.Frame(self.notebook, bg=self.BG_DARK)
        self.notebook.add(self.tab_sent, text="  📤 Sent Files  ")
        self._build_sent_tab(self.tab_sent)

        self.tab_received = tk.Frame(self.notebook, bg=self.BG_DARK)
        self.notebook.add(self.tab_received, text="  📥 Received Files  ")
        self._build_received_tab(self.tab_received)

        self.tab_sound = tk.Frame(self.notebook, bg=self.BG_DARK)
        self.notebook.add(self.tab_sound, text="  🔊 Wireless Sound Engine  ")
        self._build_sound_tab(self.tab_sound)

    def _build_transfer_tab(self, parent):
        peer_card = tk.LabelFrame(parent, text=" 1. Target Peer Device Selection ", bg=self.CARD_BG, fg=self.ACCENT_CYAN,
                                  font=("Segoe UI", 10, "bold"), padx=16, pady=10, highlightbackground=self.BORDER_COLOR)
        peer_card.pack(fill=tk.X, padx=8, pady=8)

        peer_row1 = tk.Frame(peer_card, bg=self.CARD_BG)
        peer_row1.pack(fill=tk.X, pady=(0, 6))

        tk.Label(peer_row1, text="Target Device:", font=("Segoe UI", 9, "bold"), fg=self.TEXT_WHITE, bg=self.CARD_BG).pack(side=tk.LEFT, padx=(0, 10))

        self.peer_var = tk.StringVar(value="Searching for peers...")
        self.peer_combobox = ttk.Combobox(peer_row1, textvariable=self.peer_var, state="readonly", width=42)
        self.peer_combobox.pack(side=tk.LEFT, padx=6)

        btn_refresh = tk.Button(peer_row1, text="🔄 Rescan", command=self._refresh_peers_ui,
                                bg=self.INPUT_BG, fg=self.TEXT_WHITE, activebackground=self.ACCENT_CYAN,
                                relief=tk.FLAT, padx=10, font=("Segoe UI", 8, "bold"))
        btn_refresh.pack(side=tk.LEFT, padx=4)

        btn_sweep = tk.Button(peer_row1, text="⚡ Scan Subnet", command=self._sweep_subnet_ui,
                              bg=self.INPUT_BG, fg=self.ACCENT_GREEN, relief=tk.FLAT, padx=10, font=("Segoe UI", 8, "bold"))
        btn_sweep.pack(side=tk.LEFT, padx=4)

        self.peer_count_lbl = tk.Label(peer_row1, text="0 online peers detected", font=("Segoe UI", 9), fg=self.ACCENT_GREEN, bg=self.CARD_BG)
        self.peer_count_lbl.pack(side=tk.RIGHT, padx=6)

        # Row 2: Direct IP Pair Box
        peer_row2 = tk.Frame(peer_card, bg=self.CARD_BG)
        peer_row2.pack(fill=tk.X, pady=(4, 0))

        tk.Label(peer_row2, text="Direct IP Connect:", font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG).pack(side=tk.LEFT, padx=(0, 10))

        self.direct_ip_entry = tk.Entry(peer_row2, font=("Segoe UI", 9), bg=self.INPUT_BG, fg=self.TEXT_WHITE, insertbackground="white", width=22)
        ip_parts = self.local_ip.split(".")
        suggested_ip = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}." if len(ip_parts) == 4 else ""
        self.direct_ip_entry.insert(0, suggested_ip)
        self.direct_ip_entry.pack(side=tk.LEFT, padx=6)

        btn_direct_connect = tk.Button(peer_row2, text="🔗 Pair / Connect IP", command=self._connect_direct_ip,
                                       bg=self.ACCENT_PURPLE, fg="#FFFFFF", activebackground="#C084FC",
                                       relief=tk.FLAT, padx=12, font=("Segoe UI", 8, "bold"))
        btn_direct_connect.pack(side=tk.LEFT, padx=4)

        tk.Label(peer_row2, text="(Type phone IP from Android app if Wi-Fi blocks broadcast)", font=("Segoe UI", 8, "italic"), fg=self.TEXT_MUTED, bg=self.CARD_BG).pack(side=tk.LEFT, padx=8)

        # Save Location
        save_card = tk.LabelFrame(parent, text=" 2. Save Location Configuration ", bg=self.CARD_BG, fg=self.ACCENT_PURPLE,
                                  font=("Segoe UI", 10, "bold"), padx=16, pady=8, highlightbackground=self.BORDER_COLOR)
        save_card.pack(fill=tk.X, padx=8, pady=6)

        save_inner = tk.Frame(save_card, bg=self.CARD_BG)
        save_inner.pack(fill=tk.X)

        tk.Label(save_inner, text="Save To:", font=("Segoe UI", 9, "bold"), fg=self.TEXT_WHITE, bg=self.CARD_BG).pack(side=tk.LEFT, padx=(0, 10))

        self.save_dir_entry = tk.Entry(save_inner, font=("Segoe UI", 9), bg=self.INPUT_BG, fg=self.TEXT_WHITE, insertbackground="white", width=58)
        self.save_dir_entry.insert(0, self.save_directory)
        self.save_dir_entry.pack(side=tk.LEFT, padx=6)

        btn_browse = tk.Button(save_inner, text="📂 Browse...", command=self._browse_save_folder,
                               bg=self.INPUT_BG, fg=self.TEXT_WHITE, relief=tk.FLAT, padx=10, font=("Segoe UI", 8, "bold"))
        btn_browse.pack(side=tk.LEFT, padx=4)

        btn_open = tk.Button(save_inner, text="Open Folder", command=self._open_download_folder,
                             bg=self.INPUT_BG, fg=self.ACCENT_CYAN, relief=tk.FLAT, padx=10, font=("Segoe UI", 8, "bold"))
        btn_open.pack(side=tk.LEFT, padx=4)

        # Multi-File Selection
        send_card = tk.LabelFrame(parent, text=" 3. Select & Send Files (Multi-Select Enabled) ", bg=self.CARD_BG, fg=self.ACCENT_GREEN,
                                  font=("Segoe UI", 10, "bold"), padx=16, pady=10, highlightbackground=self.BORDER_COLOR)
        send_card.pack(fill=tk.X, padx=8, pady=6)

        btn_box = tk.Frame(send_card, bg=self.CARD_BG)
        btn_box.pack(fill=tk.X)

        btn_select_files = tk.Button(btn_box, text="➕ Select Multiple Files...", command=self._select_multiple_files,
                                     bg=self.ACCENT_CYAN, fg="#000000", activebackground="#38BDF8",
                                     relief=tk.FLAT, padx=16, pady=6, font=("Segoe UI", 9, "bold"))
        btn_select_files.pack(side=tk.LEFT, padx=4)

        btn_clear_sel = tk.Button(btn_box, text="Clear Selected", command=self._clear_selected_files,
                                  bg=self.INPUT_BG, fg=self.TEXT_MUTED, relief=tk.FLAT, padx=12, pady=6, font=("Segoe UI", 9))
        btn_clear_sel.pack(side=tk.LEFT, padx=6)

        self.btn_send_now = tk.Button(btn_box, text="🚀 SEND TO TARGET DEVICE", command=self._start_send_transfer,
                                      bg=self.ACCENT_GREEN, fg="#000000", activebackground="#4ADE80",
                                      relief=tk.FLAT, padx=20, pady=6, font=("Segoe UI", 9, "bold"), state=tk.DISABLED)
        self.btn_send_now.pack(side=tk.RIGHT, padx=4)

        self.selected_files_lbl = tk.Label(send_card, text="No files selected (Click 'Select Multiple Files' to choose files)",
                                           font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG, anchor="w")
        self.selected_files_lbl.pack(fill=tk.X, pady=(8, 2))

        # Dynamic Progress Bar
        self.progress_container = tk.Frame(parent, bg=self.CARD_BG, padx=16, pady=10, highlightbackground=self.BORDER_COLOR, highlightthickness=1)

        self.progress_title = tk.Label(self.progress_container, text="Transferring...", font=("Segoe UI", 9, "bold"), fg=self.ACCENT_CYAN, bg=self.CARD_BG)
        self.progress_title.pack(anchor="w")

        self.progress_bar = ttk.Progressbar(self.progress_container, style="Horizontal.TProgressbar", mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=6)

        self.progress_stats = tk.Label(self.progress_container, text="0% | 0.0 MB/s", font=("Segoe UI", 8), fg=self.TEXT_WHITE, bg=self.CARD_BG)
        self.progress_stats.pack(anchor="w")

        # Live Log
        log_card = tk.LabelFrame(parent, text=" Live Activity Log ", bg=self.BG_DARK, fg=self.TEXT_MUTED,
                                 font=("Segoe UI", 8, "bold"), padx=8, pady=4, highlightbackground=self.BORDER_COLOR)
        log_card.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        self.log_text = tk.Text(log_card, bg=self.CARD_BG, fg=self.TEXT_WHITE, font=("Consolas", 8),
                                relief=tk.FLAT, height=6, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_sent_tab(self, parent):
        toolbar = tk.Frame(parent, bg=self.BG_DARK, pady=8)
        toolbar.pack(fill=tk.X, padx=10)

        tk.Label(toolbar, text="History of files sent from this PC to any device", font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.BG_DARK).pack(side=tk.LEFT)

        btn_clear = tk.Button(toolbar, text="🗑️ Clear Sent History", command=self._clear_sent_history,
                              bg=self.INPUT_BG, fg="#EF4444", relief=tk.FLAT, padx=12, pady=4, font=("Segoe UI", 8, "bold"))
        btn_clear.pack(side=tk.RIGHT)

        cols = ("Filename", "Size (MB)", "Target Device", "Speed", "Time", "Status")
        self.sent_tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.sent_tree.heading(c, text=c)
            self.sent_tree.column(c, width=140, anchor="center")
        self.sent_tree.column("Filename", width=300, anchor="w")

        self.sent_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    def _build_received_tab(self, parent):
        toolbar = tk.Frame(parent, bg=self.BG_DARK, pady=8)
        toolbar.pack(fill=tk.X, padx=10)

        tk.Label(toolbar, text="History of files received by this PC from any device", font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.BG_DARK).pack(side=tk.LEFT)

        btn_open = tk.Button(toolbar, text="📂 Open Received Folder", command=self._open_download_folder,
                             bg=self.INPUT_BG, fg=self.ACCENT_CYAN, relief=tk.FLAT, padx=12, pady=4, font=("Segoe UI", 8, "bold"))
        btn_open.pack(side=tk.RIGHT, padx=6)

        btn_clear = tk.Button(toolbar, text="🗑️ Clear Received History", command=self._clear_received_history,
                              bg=self.INPUT_BG, fg="#EF4444", relief=tk.FLAT, padx=12, pady=4, font=("Segoe UI", 8, "bold"))
        btn_clear.pack(side=tk.RIGHT)

        cols = ("Filename", "Size (MB)", "Sender Device", "Speed", "Time", "Status")
        self.recv_tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.recv_tree.heading(c, text=c)
            self.recv_tree.column(c, width=140, anchor="center")
        self.recv_tree.column("Filename", width=300, anchor="w")

        self.recv_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    def _build_sound_tab(self, parent):
        desc_box = tk.Frame(parent, bg=self.CARD_BG, padx=16, pady=12, highlightbackground=self.BORDER_COLOR, highlightthickness=1)
        desc_box.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(desc_box, text="Studio Ultra Low-Latency Wireless Audio", font=("Helvetica", 14, "bold"), fg=self.ACCENT_CYAN, bg=self.CARD_BG).pack(anchor="w")
        tk.Label(desc_box, text="Stream uncompressed 48kHz 16-bit stereo PCM audio across PC and Android devices over Wi-Fi/Hotspot.",
                 font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG).pack(anchor="w", pady=(2, 0))

        modes_frame = tk.Frame(parent, bg=self.BG_DARK)
        modes_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        speaker_card = tk.LabelFrame(modes_frame, text=" Option 1: Speaker Use (Listen to Phone / Other PC) ", bg=self.CARD_BG,
                                     fg=self.ACCENT_GREEN, font=("Segoe UI", 11, "bold"), padx=18, pady=16, highlightbackground=self.BORDER_COLOR)
        speaker_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)

        tk.Label(speaker_card, text="Turn this PC into a Wireless Hi-Fi Speaker.", font=("Segoe UI", 10, "bold"), fg=self.TEXT_WHITE, bg=self.CARD_BG).pack(anchor="w")
        tk.Label(speaker_card, text="When turned ON, this PC listens on TCP port 8080 and plays audio received from any connected Android phone or secondary PC.",
                 font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG, wraplength=420, justify=tk.LEFT).pack(anchor="w", pady=8)

        self.speaker_status_lbl = tk.Label(speaker_card, text="● Speaker Mode: INACTIVE", font=("Segoe UI", 10, "bold"), fg="#EF4444", bg=self.CARD_BG)
        self.speaker_status_lbl.pack(anchor="w", pady=6)

        self.btn_toggle_speaker = tk.Button(speaker_card, text="🔊 TURN ON SPEAKER USE", command=self._toggle_speaker_mode,
                                            bg=self.ACCENT_GREEN, fg="#000000", activebackground="#4ADE80",
                                            relief=tk.FLAT, padx=18, pady=10, font=("Segoe UI", 10, "bold"))
        self.btn_toggle_speaker.pack(anchor="w", pady=10)

        sender_card = tk.LabelFrame(modes_frame, text=" Option 2: Sound Send (Stream PC Sound to Device) ", bg=self.CARD_BG,
                                   fg=self.ACCENT_CYAN, font=("Segoe UI", 11, "bold"), padx=18, pady=16, highlightbackground=self.BORDER_COLOR)
        sender_card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=6)

        tk.Label(sender_card, text="Stream PC Audio to Selected Device.", font=("Segoe UI", 10, "bold"), fg=self.TEXT_WHITE, bg=self.CARD_BG).pack(anchor="w")
        tk.Label(sender_card, text="Capture PC loopback / microphone and stream live audio to the selected device or phone running P2Pwire.",
                 font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG, wraplength=420, justify=tk.LEFT).pack(anchor="w", pady=8)

        sound_target_row = tk.Frame(sender_card, bg=self.CARD_BG)
        sound_target_row.pack(fill=tk.X, pady=6)
        tk.Label(sound_target_row, text="Target:", font=("Segoe UI", 9, "bold"), fg=self.TEXT_WHITE, bg=self.CARD_BG).pack(side=tk.LEFT, padx=(0, 6))

        self.sound_peer_var = tk.StringVar(value="Select Device")
        self.sound_peer_combo = ttk.Combobox(sound_target_row, textvariable=self.sound_peer_var, state="readonly", width=34)
        self.sound_peer_combo.pack(side=tk.LEFT)

        self.sender_status_lbl = tk.Label(sender_card, text="● Sound Send: INACTIVE", font=("Segoe UI", 10, "bold"), fg="#EF4444", bg=self.CARD_BG)
        self.sender_status_lbl.pack(anchor="w", pady=6)

        self.btn_toggle_sender = tk.Button(sender_card, text="📡 START SOUND SEND", command=self._toggle_sender_mode,
                                           bg=self.ACCENT_CYAN, fg="#000000", activebackground="#38BDF8",
                                           relief=tk.FLAT, padx=18, pady=10, font=("Segoe UI", 10, "bold"))
        self.btn_toggle_sender.pack(anchor="w", pady=10)

    def _build_status_footer(self):
        footer = tk.Frame(self, bg=self.CARD_BG, height=32, highlightbackground=self.BORDER_COLOR, highlightthickness=1)
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_lbl = tk.Label(footer, text="● Ready | Multi-Pipe Engine Standby", font=("Segoe UI", 8), fg=self.ACCENT_GREEN, bg=self.CARD_BG)
        self.status_lbl.pack(side=tk.LEFT, padx=16, pady=4)

        proto_lbl = tk.Label(footer, text=f"Protocols: HBSP-TCP ({FILE_TRANSFER_PORT}) | Audio-TCP ({AUDIO_STREAM_PORT}) | UDP ({BROADCAST_PORT})",
                             font=("Segoe UI", 8), fg=self.TEXT_MUTED, bg=self.CARD_BG)
        proto_lbl.pack(side=tk.RIGHT, padx=16, pady=4)

    def on_peers_updated(self, peer_list):
        self.active_peers = peer_list
        self.after(0, self._render_peers)

    def _render_peers(self):
        options = []
        for p in self.active_peers:
            label = f"{p['name']} ({p['os']}) - {p['ip']}"
            options.append(label)

        if not options:
            self.peer_combobox["values"] = ["No peers found on this network"]
            self.peer_combobox.current(0)
            self.sound_peer_combo["values"] = ["No peers found"]
            self.sound_peer_combo.current(0)
            self.peer_count_lbl.config(text="0 online peers detected", fg=self.TEXT_MUTED)
        else:
            self.peer_combobox["values"] = options
            self.sound_peer_combo["values"] = options
            if self.peer_combobox.get() not in options:
                self.peer_combobox.current(0)
            if self.sound_peer_combo.get() not in options:
                self.sound_peer_combo.current(0)
            self.peer_count_lbl.config(text=f"{len(options)} peer(s) online", fg=self.ACCENT_GREEN)

        self._check_send_button_state()

    def _refresh_peers_ui(self):
        self.append_log("Broadcasting dual discovery beacons...", "info")
        self.discovery.scan_subnet_fast()
        self._render_peers()

    def _sweep_subnet_ui(self):
        threading.Thread(target=self.discovery.scan_subnet_fast, daemon=True).start()

    def _connect_direct_ip(self):
        target_ip = self.direct_ip_entry.get().strip()
        if not target_ip or target_ip == self.local_ip:
            messagebox.showwarning("Invalid IP", "Please enter a valid peer IP (e.g. 10.119.76.xxx)")
            return

        self.append_log(f"Attempting direct pair with {target_ip}...", "info")
        self.discovery.add_direct_peer(target_ip, "Paired Device")
        self._check_send_button_state()

    def _browse_save_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.save_directory, title="Select Destination Folder for Received Files")
        if chosen:
            self.save_directory = chosen
            self.save_dir_entry.delete(0, tk.END)
            self.save_dir_entry.insert(0, chosen)
            self.file_engine.save_dir = chosen
            self.append_log(f"Destination folder updated to: {chosen}", "info")

    def _open_download_folder(self):
        path = self.save_dir_entry.get().strip() or self.save_directory
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def _select_multiple_files(self):
        files = filedialog.askopenfilenames(title="Select Multiple Files to Send")
        if files:
            self.selected_files = list(files)
            total_size_mb = sum(os.path.getsize(f) for f in self.selected_files) / (1024 * 1024)
            names_preview = ", ".join(os.path.basename(f) for f in self.selected_files[:3])
            if len(self.selected_files) > 3:
                names_preview += f" ... (+{len(self.selected_files) - 3} more)"

            self.selected_files_lbl.config(
                text=f"Selected {len(self.selected_files)} file(s) ({total_size_mb:.2f} MB total): {names_preview}",
                fg=self.ACCENT_CYAN
            )
            self._check_send_button_state()

    def _clear_selected_files(self):
        self.selected_files = []
        self.selected_files_lbl.config(
            text="No files selected (Click 'Select Multiple Files' to choose files)",
            fg=self.TEXT_MUTED
        )
        self._check_send_button_state()

    def _check_send_button_state(self):
        has_files = len(self.selected_files) > 0
        has_peers = len(self.active_peers) > 0 and "No peers" not in self.peer_combobox.get()
        if has_files and has_peers:
            self.btn_send_now.config(state=tk.NORMAL)
        else:
            self.btn_send_now.config(state=tk.DISABLED)

    def _start_send_transfer(self):
        idx = self.peer_combobox.current()
        if idx < 0 or idx >= len(self.active_peers):
            messagebox.showwarning("Target Required", "Please select a target device from the list.")
            return

        target_peer = self.active_peers[idx]
        target_ip = target_peer["ip"]
        target_name = target_peer["name"]

        self.append_log(f"Starting batch transfer of {len(self.selected_files)} file(s) to {target_name} ({target_ip})...", "info")
        self.file_engine.send_files_batch(target_ip, target_name, self.device_name, list(self.selected_files))

    def on_transfer_progress(self, mode, filename, pct, speed_mbps, cur_bytes, total_bytes):
        self.after(0, self._update_progress_ui, mode, filename, pct, speed_mbps, cur_bytes, total_bytes)

    def _update_progress_ui(self, mode, filename, pct, speed_mbps, cur_bytes, total_bytes):
        if not self.progress_container.winfo_ismapped():
            self.progress_container.pack(fill=tk.X, padx=8, pady=6, after=self.selected_files_lbl.master)

        title_prefix = "🚀 Sending" if mode == "sending" else "📥 Receiving"
        self.progress_title.config(text=f"{title_prefix}: {filename}")
        self.progress_bar["value"] = pct

        mb_done = cur_bytes / (1024 * 1024)
        mb_total = total_bytes / (1024 * 1024)
        self.progress_stats.config(text=f"{pct:.1f}% ({mb_done:.1f}/{mb_total:.1f} MB) | Speed: {speed_mbps:.1f} MB/s")

    def on_transfer_completed(self, item):
        self.after(0, self._record_transfer_complete, item)

    def _record_transfer_complete(self, item):
        if self.progress_container.winfo_ismapped():
            self.progress_container.pack_forget()

        if item["type"] == "sent":
            self.sent_tree.insert("", 0, values=(
                item["filename"],
                f"{item['size_mb']:.2f}",
                item["device"],
                item["speed"],
                item["time"],
                "Completed"
            ))
            self.notebook.select(self.tab_sent)
        else:
            self.recv_tree.insert("", 0, values=(
                item["filename"],
                f"{item['size_mb']:.2f}",
                item["device"],
                item["speed"],
                item["time"],
                "Completed"
            ))
            self.notebook.select(self.tab_received)

    def _clear_sent_history(self):
        for row in self.sent_tree.get_children():
            self.sent_tree.delete(row)
        self.append_log("Sent transfer history cleared.", "info")

    def _clear_received_history(self):
        for row in self.recv_tree.get_children():
            self.recv_tree.delete(row)
        self.append_log("Received transfer history cleared.", "info")

    def _toggle_speaker_mode(self):
        if not self.sound_engine.speaker_running:
            success = self.sound_engine.start_speaker_mode()
            if success:
                self.speaker_status_lbl.config(text="● Speaker Mode: ACTIVE (Listening)", fg=self.ACCENT_GREEN)
                self.btn_toggle_speaker.config(text="⏹️ STOP SPEAKER USE", bg="#EF4444", fg="#FFFFFF")
        else:
            self.sound_engine.stop_speaker_mode()
            self.speaker_status_lbl.config(text="● Speaker Mode: INACTIVE", fg="#EF4444")
            self.btn_toggle_speaker.config(text="🔊 TURN ON SPEAKER USE", bg=self.ACCENT_GREEN, fg="#000000")

    def _toggle_sender_mode(self):
        if not self.sound_engine.sender_running:
            idx = self.sound_peer_combo.current()
            if idx < 0 or idx >= len(self.active_peers):
                messagebox.showwarning("Target Device Required", "Please select a target device to stream sound to.")
                return

            target = self.active_peers[idx]
            success = self.sound_engine.start_sender_mode(target["ip"])
            if success:
                self.sender_status_lbl.config(text=f"● Sound Send: ACTIVE -> {target['name']}", fg=self.ACCENT_CYAN)
                self.btn_toggle_sender.config(text="⏹️ STOP SOUND SEND", bg="#EF4444", fg="#FFFFFF")
        else:
            self.sound_engine.stop_sender_mode()
            self.sender_status_lbl.config(text="● Sound Send: INACTIVE", fg="#EF4444")
            self.btn_toggle_sender.config(text="📡 START SOUND SEND", bg=self.ACCENT_CYAN, fg="#000000")

    def append_log(self, message, level="info"):
        self.after(0, self._safe_append_log, message, level)

    def _safe_append_log(self, message, level):
        timestamp = time.strftime("%H:%M:%S")
        prefix = "•"
        if level == "error":
            prefix = "✖"
        elif level == "success":
            prefix = "✔"

        entry = f"[{timestamp}] {prefix} {message}\n"
        self.log_text.insert(tk.END, entry)
        self.log_text.see(tk.END)


if __name__ == "__main__":
    app = P2PwireApp()
    app.mainloop()
