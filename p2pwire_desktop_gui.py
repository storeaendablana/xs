# P2Pwire Premium Desktop GUI Client (CustomTkinter)
# Optimized for 100+ MB/s Gigabit Speed on 5GHz Wi-Fi and USB 3.0 Tethering
# Requirements: pip install customtkinter soundcard numpy websockets

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

BUFFER_SIZE = 1024 * 1024  # 1 MB zero-allocation high-throughput buffer
NUM_TURBO_CHANNELS = 8

class P2PwireApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("P2Pwire - Desktop Audio & Turbo Gigabit File Bridge")
        self.geometry("520x790")
        self.resizable(False, False)
        
        self.is_streaming = False
        self.loop = None
        self.thread = None
        self.is_scanning = False
        self.hbsp_cancel_flag = False
        self.selected_file_path = ctk.StringVar()
        self.transfer_progress = ctk.StringVar(value="Status: Ready for 100 MB/s Turbo Gigabit Transfer")
        self.active_turbo_sessions = {}

        self.title_label = ctk.CTkLabel(self, text="🚀 P2Pwire Turbo Desktop Bridge", font=ctk.CTkFont(size=18, weight="bold"))
        self.title_label.pack(pady=(14, 4))

        self.subtitle_label = ctk.CTkLabel(
            self,
            text="Real-time Audio Stream & 8x Parallel Sockets File Transfer (80-100+ MB/s)",
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

        # Row with Entry + Auto-Detect Button
        self.ip_input_row = ctk.CTkFrame(self.ip_frame, fg_color="transparent")
        self.ip_input_row.pack(fill="x", pady=(4, 0))

        self.ip_entry = ctk.CTkEntry(self.ip_input_row, placeholder_text="e.g. 192.168.43.1 or 192.168.1.x", height=38, font=ctk.CTkFont(size=13))
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

        # Compact log console
        self.log_box = ctk.CTkTextbox(self, height=70, font=ctk.CTkFont(size=10), text_color="#38BDF8")
        self.log_box.pack(fill="x", padx=20, pady=(0, 10))
        self.log_box.insert("0.0", "Ready. Turbo 8x HBSP on Port 8082 • Audio Stream on Port 8080\n")

        # Background auto-detect & PC receiver
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
        
        # Waveform Canvas
        self.canvas = tk.Canvas(tab, width=420, height=130, bg='#111827', highlightthickness=0)
        self.canvas.pack(pady=8)
        self.bars = [self.canvas.create_rectangle(i*10+4, 65, i*10+10, 65, fill="#34D399") for i in range(40)]
        self.pulse_text = self.canvas.create_text(210, 115, text="● Streaming 48kHz System Audio Active", fill="#34D399", font=("Arial", 10), state="hidden")

    def setup_file_tab(self):
        tab = self.tabview.tab("Turbo Files (HBSP 2.0)")

        # Protocol badge
        protocol_frame = ctk.CTkFrame(tab, fg_color="#1E293B", corner_radius=10)
        protocol_frame.pack(fill="x", padx=16, pady=(10, 8))

        ctk.CTkLabel(
            protocol_frame,
            text="⚡ Smart Auto-Max Engine • 1MB Blocks • Zero-Allocation 100 MB/s",
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

        # Smart Auto-Max Badge
        engine_frame = ctk.CTkFrame(tab, fg_color="#1E293B", corner_radius=8)
        engine_frame.pack(fill="x", padx=16, pady=(4, 6))

        ctk.CTkLabel(
            engine_frame,
            text="⚡ Transfer Engine: Smart Auto-Max",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10B981"
        ).pack(side="left", padx=10, pady=4)

        ctk.CTkLabel(
            engine_frame,
            text="● Auto-Tuned (2x - 8x Sockets)",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#38BDF8"
        ).pack(side="right", padx=10, pady=4)
        
        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4, 6))

        self.btn_send_file = ctk.CTkButton(
            btn_row,
            text="🚀 Start Turbo Gigabit Transfer",
            command=self.send_file_hbsp,
            fg_color="#10B981",
            hover_color="#059669",
            height=40,
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.btn_send_file.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_cancel_file = ctk.CTkButton(
            btn_row,
            text="✕ Cancel",
            command=self.cancel_hbsp_transfer,
            fg_color="#EF4444",
            hover_color="#DC2626",
            height=40,
            width=90,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_cancel_file.pack(side="right")

        # Open received folder button
        ctk.CTkButton(
            tab,
            text="📂 Open Received Files Folder (~/Downloads/P2PwireTransfers)",
            command=self.open_received_folder,
            height=34,
            fg_color="#334155",
            hover_color="#475569",
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(fill="x", padx=16, pady=(0, 8))

    def open_received_folder(self):
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireTransfers")
        os.makedirs(downloads_dir, exist_ok=True)
        try:
            if platform.system() == "Windows":
                os.startfile(downloads_dir)
            elif platform.system() == "Darwin":
                subprocess.run(["open", downloads_dir])
            else:
                subprocess.run(["xdg-open", downloads_dir])
            self.log(f"Opened received folder: {downloads_dir}")
        except Exception as e:
            self.log(f"Could not open folder: {e}")

    def log(self, msg):
        print(msg)
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")

    def update_waveform(self, pcm_data):
        if not self.is_streaming:
            self.canvas.itemconfig(self.pulse_text, state="hidden")
            return
            
        data = np.frombuffer(pcm_data, dtype=np.int16)
        if len(data) == 0:
            return
        
        data_chunks = np.array_split(data, 40)
        for i, chunk in enumerate(data_chunks):
            if len(chunk) == 0:
                continue
            rms = np.sqrt(np.mean(chunk.astype(np.float32)**2))
            height = min(rms / 120.0, 55.0)
            self.canvas.coords(self.bars[i], i*10+4, 65 - height, i*10+10, 65 + height)
            
        self.canvas.itemconfig(self.pulse_text, state="normal")

    def toggle_stream(self):
        if not self.is_streaming:
            ip = self.ip_entry.get().strip()
            if not ip:
                self.status_label.configure(text="● Please enter Android IP address", text_color="#EF4444")
                return
            self.is_streaming = True
            self.btn_toggle.configure(text="⏹ Stop Streaming Audio", fg_color="#EF4444", hover_color="#DC2626")
            self.status_label.configure(text="● Streaming Audio to Android Phone...", text_color="#34D399")
            self.log(f"Connecting audio stream to ws://{ip}:8080/audio...")
            self.thread = threading.Thread(target=self.start_asyncio_loop, args=(ip,), daemon=True)
            self.thread.start()
        else:
            self.is_streaming = False
            self.btn_toggle.configure(text="🔊 Start Streaming System Audio", fg_color="#06B6D4", hover_color="#0891B2")
            self.status_label.configure(text="● Stream Stopped", text_color="#FBBF24")
            self.log("Stopping audio stream...")
            for bar in self.bars:
                self.canvas.coords(bar, 0, 65, 0, 65)
            self.canvas.itemconfig(self.pulse_text, state="hidden")

    def start_asyncio_loop(self, ip):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.capture_and_stream(ip))

    async def capture_and_stream(self, ip):
        uri = f"ws://{ip}:8080/audio"
        loopback_mic = None
        try:
            mics = sc.all_microphones(include_loopback=True)
            for m in mics:
                if m.isloopback:
                    loopback_mic = m
                    break
            if not loopback_mic:
                loopback_mic = sc.default_microphone()
            self.log(f"Audio Device selected: {loopback_mic.name}")
        except Exception as e:
            self.log(f"Error accessing audio devices: {e}")
            self.after(0, self.toggle_stream)
            return

        while self.is_streaming:
            try:
                self.log(f"Connecting to {uri}...")
                async with websockets.connect(uri, ping_interval=5, ping_timeout=10, max_size=None) as websocket:
                    self.log("✓ Connected to Android Audio Server! Audio streaming live.")
                    samplerate = 48000
                    blocksize = 480
                    
                    with loopback_mic.recorder(samplerate=samplerate, channels=2, blocksize=blocksize) as mic:
                        while self.is_streaming:
                            data = mic.record(numframes=blocksize)
                            pcm16 = (data * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
                            await websocket.send(pcm16)
                            self.after(0, self.update_waveform, pcm16)
                            await asyncio.sleep(0.001)
            except Exception as e:
                if self.is_streaming:
                    self.log(f"Stream error: {e}. Reconnecting in 2s...")
                    await asyncio.sleep(2)
                else:
                    break

    def select_file(self):
        f = filedialog.askopenfilename()
        if f:
            self.selected_file_path.set(f)
            sz_mb = os.path.getsize(f) / (1024 * 1024)
            self.log(f"Selected file: {os.path.basename(f)} ({sz_mb:.2f} MB)")
            self.transfer_progress.set(f"Selected: {os.path.basename(f)} ({sz_mb:.2f} MB)")

    def cancel_hbsp_transfer(self):
        self.hbsp_cancel_flag = True
        self.log("Cancelling Turbo transfer...")

    def update_transfer_ui(self, progress, status_text):
        self.progress_bar.set(progress)
        self.transfer_progress.set(status_text)

    def recv_exact(self, sock, num_bytes):
        data = bytearray()
        while len(data) < num_bytes:
            packet = sock.recv(num_bytes - len(data))
            if not packet:
                raise ConnectionError(f"Connection closed while reading {num_bytes} bytes (got {len(data)})")
            data.extend(packet)
        return data

    def send_file_hbsp(self):
        ip = self.ip_entry.get().strip()
        file_path = self.selected_file_path.get().strip()
        if not ip:
            self.update_transfer_ui(0.0, "Please enter Android IP address")
            return
        if not file_path or not os.path.isfile(file_path):
            self.update_transfer_ui(0.0, "Please select a valid file from PC first")
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
            
            # Smart Auto-Tune channels based on file size
            if file_size < 5 * 1024 * 1024:
                num_channels = 2
            elif file_size < 40 * 1024 * 1024:
                num_channels = 4
            else:
                num_channels = 8
            session_id = random.randint(100000, 999999)

            self.log(f"Connecting to Turbo HBSP server at {ip}:8082 with {num_channels} auto-tuned parallel channels...")
            self.after(0, lambda: self.update_transfer_ui(0.01, f"Opening {num_channels} Turbo Channels to {ip}:8082..."))
            
            # 1. Connect Master Socket
            master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            master_sock.settimeout(25.0)
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
            
            self.log(f"Turbo HBSP Handshake accepted! Streaming {file_name} ({file_size/(1024*1024):.1f} MB)...")
            
            # 4. Multi-Channel Worker Threads (Lock-Free High Throughput)
            part_size = file_size // num_channels
            thread_sent = [0] * num_channels
            worker_errors = []
            
            def worker_channel(ch_idx, start_offset, length):
                wsock = None
                try:
                    wsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    wsock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    try:
                        wsock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 * 1024 * 1024)
                    except Exception:
                        pass
                    wsock.settimeout(25.0)
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
                    
                    buf = bytearray(BUFFER_SIZE)
                    mv = memoryview(buf)
                    sent = 0
                    with open(file_path, 'rb') as f:
                        f.seek(start_offset)
                        while sent < length and not self.hbsp_cancel_flag:
                            to_read = min(BUFFER_SIZE, length - sent)
                            n = f.readinto(mv[:to_read])
                            if not n:
                                break
                            wsock.sendall(mv[:n])
                            sent += n
                            thread_sent[ch_idx] = sent
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
            
            while any(t.is_alive() for t in threads) and not self.hbsp_cancel_flag:
                time.sleep(0.18)
                now = time.time()
                diff = now - last_calc_time
                if diff >= 0.2:
                    cur_up = sum(thread_sent)
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
                self.log(f"✓ File successfully transferred via Turbo in {total_time:.1f}s (Average Speed: {avg_speed:.1f} MB/s)!")
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
            srv.listen(10)
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
                self.active_turbo_sessions[session_id] = out_path
                
                ack = bytearray()
                ack.extend(HBSP_MAGIC)
                ack.append(HBSP_VERSION)
                ack.append(OP_TURBO_INIT_ACK)
                ack.extend(struct.pack('>I', session_id))
                ack.append(0)
                sock.sendall(ack)
                
                self.log(f"Turbo receiving {file_name} ({file_size/(1024*1024):.1f} MB) on {channels_cnt} channels...")
                
                comp_op = sock.recv(1)
                self.active_turbo_sessions.pop(session_id, None)
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
                
                # Poll for active session with retry loop (prevents handshake timeouts)
                target_path = self.active_turbo_sessions.get(session_id)
                start_t = time.time()
                while not target_path and (time.time() - start_t < 3.0):
                    time.sleep(0.05)
                    target_path = self.active_turbo_sessions.get(session_id)

                if not target_path:
                    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads", "P2PwireTransfers")
                    if os.path.exists(downloads_dir):
                        recent_files = sorted(
                            [os.path.join(downloads_dir, f) for f in os.listdir(downloads_dir)],
                            key=os.path.getmtime,
                            reverse=True
                        )
                        if recent_files:
                            target_path = recent_files[0]

                if target_path:
                    buf = bytearray(BUFFER_SIZE)
                    mv = memoryview(buf)
                    with open(target_path, 'r+b') as f:
                        f.seek(start_offset)
                        received = 0
                        while received < length:
                            to_read = min(BUFFER_SIZE, length - received)
                            filled = 0
                            while filled < to_read:
                                n = sock.recv_into(mv[filled:to_read])
                                if not n: break
                                filled += n
                            if filled == 0: break
                            f.write(mv[:filled])
                            received += filled
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
        if self.is_scanning:
            return
        self.btn_detect.configure(state="disabled", text="Scanning...")
        threading.Thread(target=self.detect_android_device, args=(False,), daemon=True).start()

    def detect_android_device(self, silent=False):
        self.is_scanning = True
        try:
            ip_found = None
            gateways = set()

            gateways.add("192.168.43.1")
            gateways.add("192.168.42.129")
            gateways.add("192.168.44.1")

            system_name = platform.system()
            if system_name == "Windows":
                try:
                    res = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=2)
                    for line in res.stdout.splitlines():
                        if "Default Gateway" in line or "IPv4 Address" in line:
                            parts = line.split(":")
                            if len(parts) > 1:
                                val = parts[1].strip()
                                if val.startswith("192.168.") or val.startswith("10.") or val.startswith("172."):
                                    if "Default Gateway" in line and val != "0.0.0.0":
                                        gateways.add(val)
                except Exception:
                    pass

            for gw in list(gateways):
                if self.probe_ip(gw):
                    ip_found = gw
                    break

            if not ip_found:
                subnets = set()
                for gw in gateways:
                    parts = gw.split('.')
                    if len(parts) == 4:
                        subnets.add(f"{parts[0]}.{parts[1]}.{parts[2]}")

                ips_to_test = []
                for s in subnets:
                    for i in range(1, 25):
                        ips_to_test.append(f"{s}.{i}")

                with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
                    futures = {executor.submit(self.probe_ip, ip): ip for ip in ips_to_test}
                    for future in concurrent.futures.as_completed(futures, timeout=3.0):
                        res = future.result()
                        if res:
                            ip_found = res
                            break

            if ip_found:
                self.after(0, lambda: self.on_device_found(ip_found))
            else:
                if not silent:
                    self.after(0, lambda: self.status_label.configure(
                        text="● Android not found. Verify Hotspot / USB Tethering.",
                        text_color="#EF4444"
                    ))
        except Exception as e:
            if not silent:
                self.log(f"Auto-detect error: {e}")
        finally:
            self.is_scanning = False
            self.after(0, lambda: self.btn_detect.configure(state="normal", text="🔍 Auto-Detect"))

    def on_device_found(self, ip):
        self.ip_entry.delete(0, "end")
        self.ip_entry.insert(0, ip)
        self.status_label.configure(
            text=f"● Android Device Connected at {ip} (Turbo Ready)",
            text_color="#34D399"
        )
        self.log(f"Auto-detected Android device at {ip}")

if __name__ == "__main__":
    app = P2PwireApp()
    app.mainloop()
