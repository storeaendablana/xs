# P2Pwire Premium Desktop GUI Client (CustomTkinter)
# Installation: pip install customtkinter soundcard numpy websockets
import customtkinter as ctk
import asyncio
import threading
import soundcard as sc
import numpy as np
import websockets
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
import random

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

BUFFER_SIZE = 1024 * 1024  # 1 MB buffer for maximum wireless & wire throughput

class P2PwireApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("P2Pwire - Desktop Audio & Turbo Gigabit File Bridge")
        self.geometry("520x750")
        self.resizable(False, False)
        
        self.is_streaming = False
        self.loop = None
        self.thread = None
        self.is_scanning = False
        self.hbsp_cancel_flag = False
        self.selected_file_path = ctk.StringVar()
        self.transfer_progress = ctk.StringVar(value="Status: Ready for Turbo Gigabit Transfer")

        self.title_label = ctk.CTkLabel(self, text="🚀 P2Pwire Turbo Desktop Bridge", font=ctk.CTkFont(size=18, weight="bold"))
        self.title_label.pack(pady=(14, 4))

        self.subtitle_label = ctk.CTkLabel(
            self,
            text="Real-time Audio Stream & Up to 32x Parallel Sockets IDM Engine",
            font=ctk.CTkFont(size=12),
            text_color="#94A3B8"
        )
        self.subtitle_label.pack(pady=(0, 10))

        # --- Tabs ---
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        self.tabview.add("Audio Stream")
        self.tabview.add("Turbo Files (HBSP 2.0)")

        self.setup_audio_tab()
        self.setup_file_tab()

        self.ip_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.ip_frame.pack(fill="x", padx=20, pady=4)

        self.ip_label = ctk.CTkLabel(self.ip_frame, text="Android Phone IP Address:", font=ctk.CTkFont(size=12, weight="bold"))
        self.ip_label.pack(anchor="w", padx=2)

        self.ip_input_row = ctk.CTkFrame(self.ip_frame, fg_color="transparent")
        self.ip_input_row.pack(fill="x", pady=(4, 0))

        self.ip_entry = ctk.CTkEntry(self.ip_input_row, placeholder_text="e.g. 192.168.43.1 or 10.x.x.x", height=38, font=ctk.CTkFont(size=13))
        self.ip_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.ip_entry.insert(0, "192.168.43.1")

        self.btn_detect = ctk.CTkButton(
            self.ip_input_row,
            text="🔍 Auto-Detect",
            command=self.start_auto_detect_thread,
            width=115,
            height=38,
            fg_color="#334155",
            hover_color="#475569",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_detect.pack(side="right")

        self.status_label = ctk.CTkLabel(self, text="● Status: Ready to Connect", font=ctk.CTkFont(size=12, weight="bold"), text_color="#FBBF24")
        self.status_label.pack(pady=6)

        self.log_box = ctk.CTkTextbox(self, height=75, font=ctk.CTkFont(size=10), text_color="#38BDF8")
        self.log_box.pack(fill="x", padx=20, pady=(0, 10))
        self.log_box.insert("0.0", "Ready. Up to 32x Parallel Sockets on Port 8082 • Audio Stream on Port 8080\n")

        threading.Thread(target=self.detect_android_device, args=(True,), daemon=True).start()
        threading.Thread(target=self.run_pc_hbsp_receiver, daemon=True).start()

    def setup_audio_tab(self):
        tab = self.tabview.tab("Audio Stream")
        self.btn_toggle = ctk.CTkButton(
            tab, 
            text="🔊 Start Streaming System Audio", 
            command=self.toggle_stream, 
            height=42, 
            fg_color="#06B6D4", 
            hover_color="#0891B2", 
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_toggle.pack(fill="x", padx=16, pady=(12, 8))
        
        self.canvas = tk.Canvas(tab, width=420, height=130, bg='#111827', highlightthickness=0)
        self.canvas.pack(pady=8)
        self.bars = [self.canvas.create_rectangle(i*10+4, 65, i*10+10, 65, fill="#34D399") for i in range(40)]
        self.pulse_text = self.canvas.create_text(210, 115, text="● Streaming 48kHz System Audio Active", fill="#34D399", font=("Arial", 10), state="hidden")

    def setup_file_tab(self):
        tab = self.tabview.tab("Turbo Files (HBSP 2.0)")

        protocol_frame = ctk.CTkFrame(tab, fg_color="#1E293B", corner_radius=10)
        protocol_frame.pack(fill="x", padx=16, pady=(10, 8))

        ctk.CTkLabel(
            protocol_frame,
            text="⚡ Up to 32x Parallel Sockets • 1MB Blocks • Zero-Copy Wire Speed",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10B981"
        ).pack(pady=6)

        ctk.CTkButton(
            tab,
            text="📁 Select File from PC (Movies, 4K Videos, Large Files)",
            command=self.select_file,
            height=38,
            fg_color="#334155",
            hover_color="#475569"
        ).pack(fill="x", padx=16, pady=4)

        self.file_info_label = ctk.CTkLabel(
            tab,
            textvariable=self.selected_file_path,
            wraplength=440,
            font=ctk.CTkFont(size=11),
            text_color="#CBD5E1"
        )
        self.file_info_label.pack(pady=4)
        
        self.progress_bar = ctk.CTkProgressBar(tab, progress_color="#10B981", fg_color="#1E293B", height=14)
        self.progress_bar.pack(fill="x", padx=16, pady=(8, 4))
        self.progress_bar.set(0)
        
        self.lbl_progress = ctk.CTkLabel(
            tab,
            textvariable=self.transfer_progress,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38BDF8"
        )
        self.lbl_progress.pack(pady=2)

        # IDM Sockets Selector (4x, 8x, 16x, 32x)
        sock_frame = ctk.CTkFrame(tab, fg_color="#1E293B", corner_radius=8)
        sock_frame.pack(fill="x", padx=16, pady=(4, 6))

        ctk.CTkLabel(
            sock_frame,
            text="Parallel Sockets (IDM Engine):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CBD5E1"
        ).pack(side="left", padx=10, pady=4)

        self.socket_selector = ctk.CTkSegmentedButton(
            sock_frame,
            values=["4x", "8x", "16x", "32x"],
            selected_color="#10B981",
            selected_hover_color="#059669",
            height=26
        )
        self.socket_selector.pack(side="right", padx=8, pady=4)
        self.socket_selector.set("16x")  # Defaults to 16x IDM Mode!
        
        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 10))

        self.btn_send_file = ctk.CTkButton(
            btn_row,
            text="🚀 Send via Multi-Socket Turbo Gigabit",
            command=self.send_file_hbsp,
            fg_color="#10B981",
            hover_color="#059669",
            height=42,
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.btn_send_file.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_cancel_file = ctk.CTkButton(
            btn_row,
            text="✕ Cancel",
            command=self.cancel_hbsp_transfer,
            fg_color="#EF4444",
            hover_color="#DC2626",
            height=42,
            width=90,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_cancel_file.pack(side="right")

    def log(self, msg):
        print(msg)
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")

    def update_waveform(self, pcm_data):
        if not self.is_streaming:
            self.canvas.itemconfig(self.pulse_text, state="hidden")
            return
            
        self.canvas.itemconfig(self.pulse_text, state="normal")
        audio_data = np.frombuffer(pcm_data, dtype=np.int16)
        chunk_size = len(audio_data) // 40
        if chunk_size == 0: return
        for i in range(40):
            chunk = audio_data[i*chunk_size : (i+1)*chunk_size]
            amp = np.abs(chunk).mean() / 32768 * 140
            self.canvas.coords(self.bars[i], i*10+4, 65-amp, i*10+10, 65+amp)

    def select_file(self):
        file = filedialog.askopenfilename()
        if file:
            size_mb = os.path.getsize(file) / (1024 * 1024)
            name = os.path.basename(file)
            self.selected_file_path.set(f"{name} ({size_mb:.1f} MB)")
            self._current_file_path = file

    def cancel_hbsp_transfer(self):
        self.hbsp_cancel_flag = True
        self.log("Cancelling HBSP transfer...")

    def update_transfer_ui(self, progress, status_text):
        self.progress_bar.set(progress)
        self.transfer_progress.set(status_text)

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
        master_sock = None
        try:
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)
            name_bytes = file_name.encode('utf-8')
            
            # Read user selected socket count (4x, 8x, 16x, 32x)
            mode = getattr(self, 'socket_selector', None)
            sel_val = mode.get() if mode else "16x"
            channels_map = {"4x": 4, "8x": 8, "16x": 16, "32x": 32}
            chosen_channels = channels_map.get(sel_val, 16)
            num_channels = chosen_channels if file_size > (chosen_channels * 256 * 1024) else max(1, file_size // (256 * 1024))
            session_id = random.randint(100000, 999999)

            self.log(f"Connecting to Turbo HBSP server at {ip}:8082 with {num_channels} parallel channels ({sel_val} IDM Mode)...")
            self.after(0, lambda: self.update_transfer_ui(0.01, f"Opening {num_channels} Turbo Channels ({sel_val}) to {ip}:8082..."))
            
            # 1. Connect Master Socket
            master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            master_sock.settimeout(12.0)
            master_sock.connect((ip, 8082))
            
            # 2. Send OP_TURBO_INIT
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
            
            # 3. Read OP_TURBO_INIT_ACK (11 bytes)
            ack_data = self.recv_exact(master_sock, 11)
            if not ack_data.startswith(HBSP_MAGIC) or ack_data[5] != OP_TURBO_INIT_ACK or ack_data[10] != 0:
                raise Exception(f"Turbo Handshake rejected: {ack_data}")
            
            self.log(f"Turbo HBSP Handshake accepted! Sockets allocated. Streaming {file_name} ({file_size/(1024*1024):.1f} MB)...")
            
            # 4. Multi-Channel Worker Threads
            part_size = file_size // num_channels
            uploaded_bytes = 0
            lock = threading.Lock()
            worker_errors = []
            
            def worker_channel(ch_idx, start_offset, length):
                nonlocal uploaded_bytes
                wsock = None
                try:
                    wsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    wsock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    wsock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
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
                    
                    # Stream raw 1MB binary blocks directly with zero per-chunk framing
                    sent = 0
                    with open(file_path, 'rb') as f:
                        f.seek(start_offset)
                        while sent < length and not self.hbsp_cancel_flag:
                            to_read = min(BUFFER_SIZE, length - sent)
                            chunk = f.read(to_read)
                            if not chunk:
                                break
                            wsock.sendall(chunk)
                            sent += len(chunk)
                            with lock:
                                uploaded_bytes += len(chunk)
                except Exception as ex:
                    worker_errors.append(f"Channel {ch_idx} error: {ex}")
                finally:
                    if wsock:
                        try: wsock.close()
                        except: pass

            # Launch all worker threads in parallel
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
            
            # Progress loop monitoring combined throughput across all channels
            while any(t.is_alive() for t in threads) and not self.hbsp_cancel_flag:
                time.sleep(0.18)
                now = time.time()
                diff = now - last_calc_time
                if diff >= 0.2:
                    with lock:
                        cur_up = uploaded_bytes
                    bytes_diff = cur_up - last_bytes_sample
                    current_speed = (bytes_diff / (1024 * 1024)) / diff
                    pct = cur_up / file_size if file_size > 0 else 0
                    rem_bytes = max(0, file_size - cur_up)
                    eta = int((rem_bytes / (1024 * 1024)) / current_speed) if current_speed > 0.1 else 0
                    
                    up_mb = cur_up / (1024 * 1024)
                    tot_mb = file_size / (1024 * 1024)
                    status_text = f"⚡ {pct*100:.1f}% ({up_mb:.1f} / {tot_mb:.1f} MB) • {current_speed:.1f} MB/s ({num_channels}x Turbo) • ETA: {eta}s"
                    
                    self.after(0, lambda p=pct, s=status_text: self.update_transfer_ui(p, s))
                    last_calc_time = now
                    last_bytes_sample = cur_up

            for t in threads:
                t.join(timeout=2.0)

            if self.hbsp_cancel_flag:
                self.log("Transfer cancelled by user.")
                self.after(0, lambda: self.update_transfer_ui(0.0, "Cancelled by user"))
                return

            if worker_errors:
                raise Exception("; ".join(worker_errors))

            # 5. Send OP_TURBO_COMPLETE on master socket
            master_sock.sendall(bytes([OP_TURBO_COMPLETE]))
            
            # 6. Receive OP_TURBO_COMPLETE_ACK (7 bytes)
            cack = self.recv_exact(master_sock, 7)
            if cack[5] == OP_TURBO_COMPLETE_ACK and cack[6] == 0:
                total_time = max(0.1, time.time() - start_time)
                avg_speed = (file_size / (1024 * 1024)) / total_time
                self.log(f"✓ File successfully transferred via {num_channels}x Turbo in {total_time:.1f}s (Average Speed: {avg_speed:.1f} MB/s)!")
                self.after(0, lambda: self.update_transfer_ui(1.0, f"✓ Complete! {file_size / (1024*1024):.1f} MB transferred at {avg_speed:.1f} MB/s in {total_time:.1f}s"))
            else:
                raise Exception("Turbo completion ACK failed")

        except Exception as e:
            self.log(f"Turbo HBSP Transfer Error: {e}")
            self.after(0, lambda: self.update_transfer_ui(0.0, f"Error: {e}"))
        finally:
            if master_sock:
                try: master_sock.close()
                except: pass
            self.after(0, lambda: self.btn_send_file.configure(state="normal"))

    # PC Background HBSP Receiver
    def run_pc_hbsp_receiver(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", 8082))
            srv.listen(64)
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
                
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireTransfers")
                os.makedirs(downloads_dir, exist_ok=True)
                out_path = os.path.join(downloads_dir, file_name)
                
                with open(out_path, 'wb') as f:
                    f.truncate(file_size)
                
                ack = bytearray()
                ack.extend(HBSP_MAGIC)
                ack.append(HBSP_VERSION)
                ack.append(OP_TURBO_INIT_ACK)
                ack.extend(struct.pack('>I', session_id))
                ack.append(0)
                sock.sendall(ack)
                
                self.log(f"Turbo receiving {file_name} ({file_size/(1024*1024):.1f} MB) on {channels_cnt} channels...")
                
                comp_op = sock.recv(1)
                cack = bytearray()
                cack.extend(HBSP_MAGIC)
                cack.append(HBSP_VERSION)
                cack.append(OP_TURBO_COMPLETE_ACK)
                cack.append(0)
                sock.sendall(cack)
                self.log(f"✓ Saved received file to: {out_path}")

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
                
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireTransfers")
                recent_files = sorted(
                    [os.path.join(downloads_dir, f) for f in os.listdir(downloads_dir)],
                    key=os.path.getmtime,
                    reverse=True
                )
                if recent_files:
                    target_path = recent_files[0]
                    with open(target_path, 'r+b') as f:
                        f.seek(start_offset)
                        received = 0
                        while received < length:
                            to_read = min(BUFFER_SIZE, length - received)
                            data = sock.recv(to_read)
                            if not data: break
                            f.write(data)
                            received += len(data)
        except Exception as e:
            self.log(f"PC Receiver Error: {e}")
        finally:
            try: sock.close()
            except: pass

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
        if self.is_scanning: return
        threading.Thread(target=self.detect_android_device, args=(False,), daemon=True).start()

    def detect_android_device(self, silent_startup=False):
        self.is_scanning = True
        if not silent_startup:
            self.after(0, lambda: self.btn_detect.configure(text="⏳ Scanning...", state="disabled"))
            self.after(0, lambda: self.status_label.configure(text="● Scanning local network for Android Phone...", text_color="#38BDF8"))
            self.log("Searching for Android P2Pwire receiver...")
        
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
            self.after(0, lambda: self.status_label.configure(text=f"● Android Phone Found: {found_ip}", text_color="#34D399"))
            self.log(f"Auto-Detected: {found_ip}")
        else:
            if not silent_startup:
                self.after(0, lambda: self.status_label.configure(text="● Phone not found on network. Enter IP manually.", text_color="#F87171"))
                self.log("Auto-detection finished: No active P2Pwire phone found on local subnet.")
        self.after(0, lambda: self.btn_detect.configure(text="🔍 Auto-Detect", state="normal"))
        return found_ip

    def toggle_stream(self):
        if not self.is_streaming:
            ip = self.ip_entry.get().strip()
            if not ip:
                self.status_label.configure(text="● Auto-Detecting Android IP...", text_color="#22D3EE")
                ip = self.detect_android_device(silent_startup=False)
                if not ip: return

            self.is_streaming = True
            self.btn_toggle.configure(text="⏹️ Stop Streaming", fg_color="#EF4444", hover_color="#DC2626")
            self.status_label.configure(text=f"● Connecting to {ip}...", text_color="#22D3EE")
            self.log(f"Attempting connection to ws://{ip}:8080/ws/upload_audio...")
            
            self.thread = threading.Thread(target=self.run_async_stream, args=(ip,), daemon=True)
            self.thread.start()
        else:
            self.is_streaming = False
            self.btn_toggle.configure(text="🔊 Start Streaming System Audio", fg_color="#06B6D4", hover_color="#0891B2")
            self.status_label.configure(text="● Status: Disconnected", text_color="#FBBF24")
            self.log("Streaming stopped by user.")
            self.canvas.itemconfig(self.pulse_text, state="hidden")

    def run_async_stream(self, ip):
        async def stream():
            uri = f"ws://{ip}:8080/ws/upload_audio"
            reconnect_attempts = 0

            while self.is_streaming:
                try:
                    self.log(f"Opening WebSocket connection to {uri}...")
                    async with websockets.connect(
                        uri,
                        ping_interval=20,
                        ping_timeout=15,
                        close_timeout=5,
                        max_size=None
                    ) as ws:
                        reconnect_attempts = 0
                        self.after(0, lambda: self.status_label.configure(text="● Connected! Initializing Audio Loopback...", text_color="#34D399"))
                        self.log("WebSocket connection established successfully!")

                        speaker = sc.default_speaker()
                        mic = sc.get_microphone(id=speaker.name, include_loopback=True)
                        self.log("Loopback audio capture device initialized successfully.")

                        self.after(0, lambda: self.status_label.configure(text="● Streaming Live 48kHz System Audio...", text_color="#34D399"))

                        audio_queue = asyncio.Queue(maxsize=15)
                        stop_capture_event = threading.Event()

                        def audio_producer():
                            try:
                                with mic.recorder(samplerate=48000, channels=2) as recorder:
                                    while self.is_streaming and not stop_capture_event.is_set():
                                        data = recorder.record(numframes=960)
                                        mono = np.mean(data, axis=1)
                                        d = np.clip(mono, -1.0, 1.0)
                                        pcm16 = (d * 32767).astype(np.int16).tobytes()

                                        if audio_queue.full():
                                            try:
                                                audio_queue.get_nowait()
                                            except Exception:
                                                pass
                                        self.loop.call_soon_threadsafe(audio_queue.put_nowait, pcm16)
                            except Exception as capture_err:
                                if self.is_streaming and not stop_capture_event.is_set():
                                    self.log(f"[CAPTURE ERROR] {capture_err}")

                        capture_thread = threading.Thread(target=audio_producer, daemon=True)
                        capture_thread.start()

                        seq = 0
                        stopped_by_phone = False
                        while self.is_streaming:
                            pcm16 = await audio_queue.get()
                            h = bytearray(8)
                            h[0], h[1], h[2] = 0xAB, 0x46, 0x01
                            h[3], h[4] = (seq >> 8) & 0xFF, seq & 0xFF
                            seq = (seq + 1) & 0xFFFF
                            try:
                                await ws.send(bytes(h) + pcm16)
                                self.after(0, self.update_waveform, pcm16)
                            except websockets.exceptions.ConnectionClosedOK:
                                stopped_by_phone = True
                                self.log("Audio playback stopped by Android phone.")
                                break
                            except Exception as send_ex:
                                self.log(f"Stream send interrupted: {send_ex}")
                                break

                        stop_capture_event.set()
                        if stopped_by_phone or not self.is_streaming:
                            self.is_streaming = False
                            break

                except Exception as e:
                    err_msg = str(e)
                    self.log(f"[STATUS] {err_msg}")

                if not self.is_streaming:
                    break

                reconnect_attempts += 1
                if self.is_streaming:
                    backoff = 2 if reconnect_attempts < 5 else 3
                    self.log(f"Connection lost. Reconnecting in {backoff}s (Attempt #{reconnect_attempts})...")
                    self.after(0, lambda: self.status_label.configure(text=f"● Reconnecting (Attempt #{reconnect_attempts})...", text_color="#FBBF24"))
                    await asyncio.sleep(backoff)
                else:
                    break

            self.after(0, lambda: self.status_label.configure(text="● Stopped / Ready to Connect", text_color="#FBBF24"))
            self.after(0, lambda: self.btn_toggle.configure(text="🔊 Start Streaming System Audio", fg_color="#06B6D4", hover_color="#0891B2"))
            self.after(0, lambda: self.status_label.configure(text="● Status: Ready to Connect", text_color="#34D399"))

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(stream())
        except Exception as ex:
            self.log(f"[FATAL] {ex}")

if __name__ == "__main__":
    app = P2PwireApp()
    app.mainloop()
