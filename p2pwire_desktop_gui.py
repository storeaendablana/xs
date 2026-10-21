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

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class P2PwireApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("P2Pwire - Desktop System Audio Bridge")
        self.geometry("500x410")
        self.resizable(False, False)
        
        self.is_streaming = False
        self.loop = None
        self.thread = None
        self.is_scanning = False

        self.title_label = ctk.CTkLabel(self, text="🚀 P2Pwire System Audio Bridge", font=ctk.CTkFont(size=18, weight="bold"))
        self.title_label.pack(pady=(16, 6))

        self.subtitle_label = ctk.CTkLabel(self, text="Stream 100% pure system audio to your Android phone", font=ctk.CTkFont(size=12), text_color="#94A3B8")
        self.subtitle_label.pack(pady=(0, 12))

        self.ip_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.ip_frame.pack(fill="x", padx=25, pady=4)

        self.ip_label = ctk.CTkLabel(self.ip_frame, text="Android Phone IP Address:", font=ctk.CTkFont(size=13, weight="bold"))
        self.ip_label.pack(anchor="w", padx=2)

        # Row with Entry + Auto-Detect Button
        self.ip_input_row = ctk.CTkFrame(self.ip_frame, fg_color="transparent")
        self.ip_input_row.pack(fill="x", pady=(5, 0))

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
        self.status_label.pack(pady=10)

        self.btn_toggle = ctk.CTkButton(
            self, 
            text="🔊 Start Streaming System Audio", 
            command=self.toggle_stream, 
            height=45, 
            fg_color="#06B6D4", 
            hover_color="#0891B2", 
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_toggle.pack(fill="x", padx=25, pady=6)

        # Compact log console for connection details
        self.log_box = ctk.CTkTextbox(self, height=36, font=ctk.CTkFont(size=10), text_color="#38BDF8")
        self.log_box.pack(fill="x", padx=25, pady=(0, 10))
        self.log_box.insert("0.0", "Ready. Connection status and logs appear here...\n")

        # Launch background auto-detect on startup
        threading.Thread(target=self.detect_android_device, args=(True,), daemon=True).start()

    def log(self, msg):
        print(msg)
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")

    def probe_ip(self, ip):
        """Quickly checks if an IP is running the P2Pwire server on port 8080"""
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
        threading.Thread(target=self.detect_android_device, args=(False,), daemon=True).start()

    def detect_android_device(self, silent_startup=False):
        self.is_scanning = True
        if not silent_startup:
            self.after(0, lambda: self.btn_detect.configure(text="⏳ Scanning...", state="disabled"))
            self.after(0, lambda: self.status_label.configure(text="● Scanning local network for Android Phone...", text_color="#38BDF8"))
            self.log("Searching for Android P2Pwire receiver...")

        # 1. Hotspot Default Gateway / Common Tether IPs (Hotspot mode: Instant 5ms)
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

        # Standard Android Hotspot & USB tethering gateway addresses
        for gw in ["192.168.43.1", "192.168.44.1", "192.168.50.1", "192.168.42.129"]:
            if gw not in candidates:
                candidates.append(gw)

        found_ip = None
        for cand in candidates:
            if self.probe_ip(cand):
                found_ip = cand
                break

        # 2. Local Wi-Fi subnet scan (if phone & laptop are on the same router)
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
            self.log(f"Auto-Detected Android Device IP: {found_ip}")
        else:
            if not silent_startup:
                self.after(0, lambda: self.status_label.configure(text="● Phone not found on network. Enter IP manually.", text_color="#F87171"))
                self.log("Auto-detection finished: No active P2Pwire phone found on local subnet.")

        self.after(0, lambda: self.btn_detect.configure(text="🔍 Auto-Detect", state="normal"))
        return found_ip

    def toggle_stream(self):
        if not self.is_streaming:
            ip = self.ip_entry.get().strip()
            # If IP is empty, auto-detect first
            if not ip:
                self.status_label.configure(text="● Auto-Detecting Android IP...", text_color="#22D3EE")
                ip = self.detect_android_device(silent_startup=False)
                if not ip:
                    return

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

    def run_async_stream(self, ip):
        async def stream():
            uri = f"ws://{ip}:8080/ws/upload_audio"
            reconnect_attempts = 0
            max_reconnects = 10

            while self.is_streaming:
                try:
                    self.log(f"Opening WebSocket connection to {uri}...")
                    # 20s Ping-Pong Heartbeat prevents Wi-Fi AP, hotspot NAT, and router idle drops
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
                        self.log(f"Default speaker detected: {speaker.name}")
                        mic = sc.get_microphone(id=speaker.name, include_loopback=True)
                        self.log("Loopback audio capture device initialized successfully.")

                        self.after(0, lambda: self.status_label.configure(text="● Streaming Live 48kHz System Audio...", text_color="#34D399"))

                        # Decoupled Queue: Network delay won't stutter system audio
                        audio_queue = asyncio.Queue(maxsize=15)
                        stop_capture_event = threading.Event()

                        # Dedicated OS Thread for 48kHz audio capture (960 frames = 20ms chunks)
                        def audio_producer():
                            try:
                                with mic.recorder(samplerate=48000, channels=2) as recorder:
                                    while self.is_streaming and not stop_capture_event.is_set():
                                        data = recorder.record(numframes=960)
                                        mono = np.mean(data, axis=1)
                                        d = np.clip(mono, -1.0, 1.0)
                                        pcm16 = (d * 32767).astype(np.int16).tobytes()

                                        # If network buffer is full, drop oldest chunk to ensure absolute zero-lag
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

                # Persistent continuous auto-reconnection (keeps retrying if phone screen locks or Wi-Fi blips)
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
