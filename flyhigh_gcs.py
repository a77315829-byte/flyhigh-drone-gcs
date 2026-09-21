import subprocess
import time
import os
import threading
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox
import tkintermapview
from pymavlink import mavutil
import atexit
import sys
import traceback
import requests  # type: ignore[import-untyped]
from PIL import Image, ImageTk

# Pillow 버전별 리샘플링 상수 호환 처리
RESAMPLE_BICUBIC = getattr(getattr(Image, "Resampling", Image), "BICUBIC")
import sqlite3
import math
import csv
import json
import re
from datetime import datetime


current_dir = os.path.dirname(os.path.abspath(__file__))


# ===============================================================
# 1. 지형 고도 계산
# ===============================================================
DEFAULT_ELEVATION = 30


def get_elevation(lat, lon):
    url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"

    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data["results"][0]["elevation"]

    except Exception as e:
        print("[ELEVATION ERROR]", e)
        return DEFAULT_ELEVATION


# ===============================================================
# 2. SITL 관리 클래스
# ===============================================================
class SITLManager:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.SITL_PATH = os.path.join(current_dir, "sitl")

    def read_output(self, proc):
        try:
            if proc and proc.stdout:
                for line in proc.stdout:
                    print("[SITL]", line.strip())
        except Exception as e:
            print("[SITL OUTPUT ERROR]", e)

    def is_running(self):
        with self.lock:
            return self.proc is not None and self.proc.poll() is None

    def start(self, lat, lon):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                print("[WARN] SITL is already running.")
                return False

            exe_path = os.path.join(self.SITL_PATH, "ArduCopter.exe")

            if not os.path.isdir(self.SITL_PATH):
                print("[PATH ERROR] SITL 폴더를 찾을 수 없습니다:", self.SITL_PATH)
                return False

            if not os.path.isfile(exe_path):
                print("[PATH ERROR] ArduCopter.exe를 찾을 수 없습니다:", exe_path)
                return False

        terrain = get_elevation(lat, lon)

        cmd = [
            exe_path,
            "-S",
            "-I0",
            "--model", "quad",
            "--home", f"{lat},{lon},{terrain},0"
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.SITL_PATH,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            with self.lock:
                self.proc = proc

            threading.Thread(target=self.read_output, args=(proc,), daemon=True).start()
            return True

        except Exception as e:
            print("[SITL START ERROR]", e)

            with self.lock:
                self.proc = None

            return False

    def stop(self):
        with self.lock:
            proc = self.proc
            self.proc = None

        if proc:
            print("[SITL] STOP")

            try:
                if proc.poll() is None:
                    proc.terminate()

                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        print("[SITL] terminate timeout, killing process")
                        proc.kill()
                        proc.wait()

            except Exception as e:
                print("[SITL STOP ERROR]", e)


# ===============================================================
# 3. 지도 선택 팝업창 클래스
# ===============================================================
class MapSelectionDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.title("지도 선택")
        self.geometry("400x200")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", lambda: sys.exit(0))

        self.result = None

        self.map_options = {
            "1. openstreetmap": (
                "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "openstreetmap.db",
                19
            ),
            "2. basemaps.cartocdn": (
                "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
                "carto_light.db",
                19
            ),
            "3. basemaps.cartocdn dark": (
                "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
                "carto_dark.db",
                19
            ),
            "4. opentopomap": (
                "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
                "opentopo.db",
                17
            ),
            "5. Esri Satellite": (
                "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                "esri_sat.db",
                19
            ),
            "6. Google Hybrid": (
                "https://mt0.google.com/vt/lyrs=y&hl=en&x={x}&y={y}&z={z}",
                "google_hybrid.db",
                19
            )
        }

        tk.Label(
            self,
            text="사용할 지도를 선택해 주세요.",
            font=("맑은 고딕", 12, "bold")
        ).pack(pady=20)

        self.combo = ttk.Combobox(
            self,
            values=list(self.map_options.keys()),
            state="readonly",
            width=35
        )
        self.combo.current(0)
        self.combo.pack(pady=10)

        tk.Button(
            self,
            text="확인 및 실행",
            width=20,
            bg="#2ecc71",
            fg="white",
            font=("맑은 고딕", 10, "bold"),
            command=self.on_confirm
        ).pack(pady=15)

        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (self.winfo_width() // 2)
        y = (self.winfo_screenheight() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

    def on_confirm(self):
        selected = self.combo.get()
        self.result = self.map_options[selected]
        self.destroy()


# ===============================================================
# 4. 이륙 고도 입력 팝업 클래스
# ===============================================================
class TakeoffDialog(tk.Toplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback

        self.title("이륙 고도 설정")
        self.geometry("320x150")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.transient(parent)
        self.grab_set()

        tk.Label(
            self,
            text="이륙 고도(m)를 입력하세요.",
            font=("맑은 고딕", 11, "bold")
        ).pack(pady=(18, 5))

        self.alt_entry = tk.Entry(self, justify="center", font=("맑은 고딕", 11))
        self.alt_entry.insert(0, "10")
        self.alt_entry.pack(pady=5)
        self.alt_entry.focus_set()

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)

        tk.Button(
            btn_frame,
            text="이륙",
            width=10,
            bg="#3498db",
            fg="white",
            command=self.on_ok
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame,
            text="취소",
            width=10,
            command=self.destroy
        ).pack(side="left", padx=5)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - 75
        self.geometry(f"+{int(x)}+{int(y)}")

    def on_ok(self):
        try:
            alt = float(self.alt_entry.get())
            if alt <= 0:
                raise ValueError
            self.callback(alt)
            self.destroy()
        except Exception:
            messagebox.showwarning("입력 오류", "올바른 이륙 고도를 입력하세요.")


# ===============================================================
# 5. Guided 이동 고도 입력 팝업 클래스
# ===============================================================
class GuidedMoveDialog(tk.Toplevel):
    def __init__(self, parent, callback, lat, lon):
        super().__init__(parent)
        self.callback = callback
        self.lat = lat
        self.lon = lon

        self.title("목적지 고도 설정")
        self.geometry("350x150")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.transient(parent)
        self.grab_set()

        tk.Label(
            self,
            text=f"목표 좌표:\n{lat:.6f}, {lon:.6f}",
            fg="blue",
            font=("맑은 고딕", 9)
        ).pack(pady=5)

        tk.Label(self, text="이동 고도(m):", font=("맑은 고딕", 10)).pack()
        self.alt_entry = tk.Entry(self, justify="center")
        self.alt_entry.insert(0, "20")
        self.alt_entry.pack(pady=5)
        self.alt_entry.focus_set()

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=5)

        tk.Button(
            btn_frame,
            text="이동 명령",
            width=12,
            bg="#3498db",
            fg="white",
            command=self.on_ok
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame,
            text="취소",
            width=10,
            command=self.destroy
        ).pack(side="left", padx=5)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - 75
        self.geometry(f"+{int(x)}+{int(y)}")

    def on_ok(self):
        try:
            alt = float(self.alt_entry.get())
            if alt < 0.5:
                raise ValueError
            self.callback(self.lat, self.lon, alt)
            self.destroy()
        except Exception:
            messagebox.showwarning("입력 오류", "올바른 숫자를 입력하세요.")



# ===============================================================
# 6. 자동으로 사라지는 안내 문구 클래스
# ===============================================================
class ToastMessage(tk.Toplevel):
    def __init__(self, parent, message, duration=2500):
        super().__init__(parent)

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#333333")

        label = tk.Label(
            self,
            text=message,
            fg="white",
            bg="#333333",
            font=("맑은 고딕", 11, "bold"),
            padx=20,
            pady=12
        )
        label.pack()

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{int(x)}+{int(y)}")

        self.after(duration, self.destroy)


# ===============================================================
# 6. HUD 클래스
# ===============================================================
class GCSHUD(tk.Canvas):
    def __init__(self, parent, width=300, height=220):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg="#1c1c1c",
            highlightthickness=1,
            highlightbackground="#00ff00"
        )

        self.w = width
        self.h = height
        self.cx = width / 2
        self.cy = height / 2

        self.create_oval(
            self.cx - 70,
            self.cy - 70,
            self.cx + 70,
            self.cy + 70,
            fill="#1e90ff",
            outline="white",
            tags="horizon_bg"
        )

        self.create_arc(
            self.cx - 70,
            self.cy - 70,
            self.cx + 70,
            self.cy + 70,
            start=0,
            extent=-180,
            fill="#8b4513",
            outline="white",
            style=tk.CHORD,
            tags="horizon_ground"
        )

        self.create_line(
            self.cx - 70,
            self.cy,
            self.cx + 70,
            self.cy,
            fill="#00ff00",
            width=2,
            tags="horizon_line"
        )

        self.create_line(self.cx - 25, self.cy, self.cx - 10, self.cy, fill="white", width=3)
        self.create_line(self.cx + 10, self.cy, self.cx + 25, self.cy, fill="white", width=3)
        self.create_line(self.cx, self.cy, self.cx, self.cy + 5, fill="white", width=3)

        self.create_text(
            self.w / 2,
            10,
            text="상태 : ",
            fill="white",
            font=("맑은 고딕", 10, "bold"),
            anchor="n"
        )

        self.arm_text = self.create_text(
            60,
            10,
            text="DISARM",
            fill="red",
            font=("맑은 고딕", 10, "bold"),
            anchor="nw"
        )

        self.create_text(
            self.w - 95,
            10,
            text="배터리 : ",
            fill="white",
            font=("맑은 고딕", 10, "bold"),
            anchor="ne"
        )

        self.bat_text = self.create_text(
            self.w - 10,
            10,
            text="0.0V (0%)",
            fill="#00ff00",
            font=("맑은 고딕", 10, "bold"),
            anchor="ne"
        )

        self.create_text(35, self.cy - 25, text="지상속도", fill="white", font=("맑은 고딕", 10))
        self.speed_val = self.create_text(35, self.cy, text="0", fill="#00ff00", font=("Consolas", 14, "bold"))
        self.create_text(35, self.cy + 20, text="m/s", fill="white", font=("맑은 고딕", 10))

        self.create_text(self.w - 35, self.cy - 25, text="상대고도", fill="white", font=("맑은 고딕", 10))
        self.alt_val = self.create_text(self.w - 35, self.cy, text="0", fill="#00ff00", font=("Consolas", 14, "bold"))
        self.create_text(self.w - 35, self.cy + 20, text="m (AGL)", fill="white", font=("맑은 고딕", 10))

        self.create_text(
            10,
            self.h - 10,
            text="위성수 : ",
            fill="white",
            font=("맑은 고딕", 10, "bold"),
            anchor="sw"
        )

        self.sat_text = self.create_text(
            65,
            self.h - 10,
            text="0",
            fill="#00ff00",
            font=("맑은 고딕", 10, "bold"),
            anchor="sw"
        )

        self.create_text(
            self.w - 80,
            self.h - 10,
            text="모드 : ",
            fill="white",
            font=("맑은 고딕", 10, "bold"),
            anchor="se"
        )

        self.mode_text = self.create_text(
            self.w - 10,
            self.h - 10,
            text="NONE",
            fill="#00ff00",
            font=("맑은 고딕", 10, "bold"),
            anchor="se"
        )

    def update_hud(self, roll, pitch, alt, speed, mode, sats, bat_v, bat_p, is_armed):
        rad_roll = math.radians(roll)
        p_off = pitch * 1.2

        x1 = self.cx - 70 * math.cos(rad_roll)
        y1 = self.cy + p_off - 70 * math.sin(rad_roll)
        x2 = self.cx + 70 * math.cos(rad_roll)
        y2 = self.cy + p_off + 70 * math.sin(rad_roll)

        self.coords("horizon_line", x1, y1, x2, y2)

        if is_armed:
            self.itemconfig(self.arm_text, text="ARM", fill="#00fa00")
        else:
            self.itemconfig(self.arm_text, text="DISARM", fill="red")

        self.itemconfig(self.bat_text, text=f"{bat_v:.1f}V ({bat_p}%)")
        self.itemconfig(self.speed_val, text=f"{speed:.1f}")
        self.itemconfig(self.alt_val, text=f"{int(alt)}")
        self.itemconfig(self.sat_text, text=f"{sats}")
        self.itemconfig(self.mode_text, text=f"{mode}")



# ===============================================================
# 7. AI Route Assist 팝업 클래스
# ===============================================================
class AIRouteAssistDialog(tk.Toplevel):
    """Ollama/규칙 기반 AI 주행 보조 계획 팝업"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Preset 2 - AI Route Assist")
        self.geometry("700x720")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.transient(parent)

        self.configure(bg="#0b1320")

        title = tk.Label(
            self,
            text="AI Route Assist Mode",
            bg="#0b1320",
            fg="#80ffdb",
            font=("맑은 고딕", 17, "bold")
        )
        title.pack(anchor="w", padx=18, pady=(14, 0))

        sub = tk.Label(
            self,
            text="목표는 지도 클릭, 위도/경도 직접 입력, 또는 주소→좌표 변환으로 설정합니다. Ollama는 비행 요청 의도만 분석하고, 안전고도/배터리/건물 계산은 GCS 규칙 엔진이 처리합니다.",
            bg="#0b1320",
            fg="#b8c7d9",
            font=("맑은 고딕", 9),
            wraplength=570,
            justify="left"
        )
        sub.pack(anchor="w", padx=18, pady=(2, 10))

        form = tk.Frame(self, bg="#132235", highlightthickness=1, highlightbackground="#2f6f91")
        form.pack(fill="x", padx=18, pady=8)

        tk.Label(form, text="자연어 비행 요청", bg="#132235", fg="white", font=("맑은 고딕", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.prompt_text = tk.Text(form, height=4, bg="#0b1320", fg="#eaf6ff", insertbackground="white", font=("맑은 고딕", 10), relief="flat")
        self.prompt_text.pack(fill="x", padx=10, pady=(0, 8))
        self.prompt_text.insert("1.0", "건물은 피하고 배터리를 아끼면서 목표 지점까지 이동해줘")

        row = tk.Frame(form, bg="#132235")
        row.pack(fill="x", padx=10, pady=4)
        tk.Label(row, text="Ollama 모델", bg="#132235", fg="#b8c7d9", width=12, anchor="w").pack(side="left")
        self.model_entry = tk.Entry(row, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.model_entry.insert(0, "llama3.2")
        self.model_entry.pack(side="left", fill="x", expand=True)

        address_row = tk.Frame(form, bg="#132235")
        address_row.pack(fill="x", padx=10, pady=4)
        tk.Label(address_row, text="주소/장소명", bg="#132235", fg="#b8c7d9", width=12, anchor="w").pack(side="left")
        self.address_entry = tk.Entry(address_row, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.address_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.address_entry.insert(0, "서울특별시 강남구 압구정동 387")
        tk.Button(
            address_row,
            text="주소 → 좌표",
            command=self.on_geocode_address,
            bg="#ffd166",
            fg="#06111f",
            activebackground="#f0b429",
            relief="flat",
            font=("맑은 고딕", 9, "bold"),
            width=11
        ).pack(side="left")

        address_help = tk.Label(
            form,
            text="※ 구글맵에서 복사한 주소를 붙여넣고 [주소 → 좌표]를 누르면 목표 위도/경도가 자동 입력됩니다.",
            bg="#132235",
            fg="#94a9c4",
            font=("맑은 고딕", 8),
            anchor="w",
            justify="left"
        )
        address_help.pack(fill="x", padx=10, pady=(0, 3))

        coord_row = tk.Frame(form, bg="#132235")
        coord_row.pack(fill="x", padx=10, pady=4)
        tk.Label(coord_row, text="목표 위도", bg="#132235", fg="#b8c7d9", width=10, anchor="w").pack(side="left")
        self.lat_entry = tk.Entry(coord_row, width=14, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.lat_entry.pack(side="left", padx=(0, 8))
        tk.Label(coord_row, text="목표 경도", bg="#132235", fg="#b8c7d9", width=10, anchor="w").pack(side="left")
        self.lon_entry = tk.Entry(coord_row, width=14, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.lon_entry.pack(side="left")

        # 이미 TARGET 마커가 있으면 그 좌표를 기본값으로 사용한다.
        lat, lon = parent.get_current_ai_target_or_default()
        self.lat_entry.insert(0, f"{lat:.6f}")
        self.lon_entry.insert(0, f"{lon:.6f}")

        opt_row = tk.Frame(form, bg="#132235")
        opt_row.pack(fill="x", padx=10, pady=(4, 10))
        tk.Label(opt_row, text="안전여유(m)", bg="#132235", fg="#b8c7d9", width=10, anchor="w").pack(side="left")
        self.margin_entry = tk.Entry(opt_row, width=8, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.margin_entry.insert(0, "15")
        self.margin_entry.pack(side="left", padx=(0, 10))
        tk.Label(opt_row, text="최대고도(m)", bg="#132235", fg="#b8c7d9", width=10, anchor="w").pack(side="left")
        self.max_alt_entry = tk.Entry(opt_row, width=8, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.max_alt_entry.insert(0, "80")
        self.max_alt_entry.pack(side="left")

        btn_row = tk.Frame(self, bg="#0b1320")
        btn_row.pack(fill="x", padx=18, pady=8)

        tk.Button(btn_row, text="Ollama 분석", command=self.on_analyze_ollama, bg="#00d4ff", fg="#06111f", relief="flat", font=("맑은 고딕", 10, "bold"), height=2).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(btn_row, text="규칙 기반 분석", command=self.on_analyze_rule, bg="#80ffdb", fg="#06111f", relief="flat", font=("맑은 고딕", 10, "bold"), height=2).pack(side="left", fill="x", expand=True, padx=4)
        tk.Button(btn_row, text="TARGET 불러오기", command=self.load_current_target, bg="#8ecae6", fg="#06111f", relief="flat", font=("맑은 고딕", 10, "bold"), height=2).pack(side="left", fill="x", expand=True, padx=4)
        tk.Button(btn_row, text="추천 고도 실행", command=self.on_execute, bg="#ff4d6d", fg="white", relief="flat", font=("맑은 고딕", 10, "bold"), height=2).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.result_text = tk.Text(self, height=16, bg="#07111f", fg="#eaf6ff", insertbackground="white", font=("Consolas", 10), relief="flat")
        self.result_text.pack(fill="both", expand=True, padx=18, pady=(6, 12))
        self.write_result("분석 대기 중입니다.\n\n1) 지도에서 TARGET을 찍거나 목표 좌표를 직접 입력하세요.\n2) 주소만 있는 경우 주소/장소명 칸에 붙여넣고 [주소 → 좌표]를 누르세요.\n3) Ollama 분석은 목표 위도/경도를 만들어내는 기능이 아닙니다.\n4) Ollama는 '건물 회피 / 배터리 절약 / 안전여유' 같은 의도를 JSON으로 정리합니다.\n5) 실제 안전고도와 배터리 효율 점수는 GCS 규칙 엔진이 계산합니다.\n6) Ollama가 없으면 '규칙 기반 분석'만으로도 고도 추천이 가능합니다.")

    def load_current_target(self):
        """지도 TARGET 또는 현재 위치 기반 기본 좌표를 목표 입력칸에 반영한다."""
        try:
            lat, lon = self.parent.get_current_ai_target_or_default()
            self.lat_entry.delete(0, tk.END)
            self.lat_entry.insert(0, f"{lat:.6f}")
            self.lon_entry.delete(0, tk.END)
            self.lon_entry.insert(0, f"{lon:.6f}")
            self.write_result(
                f"현재 TARGET 좌표를 불러왔습니다.\n\n"
                f"목표 위도: {lat:.6f}\n"
                f"목표 경도: {lon:.6f}\n\n"
                f"이 좌표를 기준으로 Ollama 분석 또는 규칙 기반 분석을 실행할 수 있습니다."
            )
        except Exception as e:
            messagebox.showwarning("TARGET 불러오기 오류", str(e))

    def on_geocode_address(self):
        """주소/장소명 입력값을 위도/경도로 변환한다."""
        address = self.address_entry.get().strip()

        if not address:
            messagebox.showwarning("주소 입력 오류", "주소 또는 장소명을 입력하세요.")
            return

        # 좌표 문자열을 붙여넣은 경우에는 API 호출 없이 바로 파싱한다.
        parsed = self.try_parse_coordinate_text(address)
        if parsed:
            lat, lon = parsed
            self.apply_geocoded_target(lat, lon, "좌표 직접 입력")
            return

        self.write_result(
            "주소를 위도/경도로 변환 중입니다...\n\n"
            f"입력 주소: {address}\n\n"
            "인터넷 연결이 필요합니다. 결과가 부정확하면 주소를 더 구체적으로 입력하세요."
        )

        threading.Thread(
            target=self._geocode_worker,
            args=(address,),
            daemon=True
        ).start()

    def try_parse_coordinate_text(self, text):
        """'37.123, 127.123' 형태의 좌표 문자열을 파싱한다."""
        try:
            cleaned = (
                text.replace(",", " ")
                .replace("위도", " ")
                .replace("경도", " ")
                .replace("lat", " ")
                .replace("lon", " ")
                .replace("lng", " ")
                .replace(":", " ")
                .replace("=", " ")
            )
            parts = [p for p in cleaned.split() if p.strip()]

            nums = []
            for p in parts:
                try:
                    nums.append(float(p))
                except Exception:
                    pass

            if len(nums) >= 2:
                lat, lon = nums[0], nums[1]
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon
        except Exception:
            pass

        return None

    def _geocode_worker(self, address):
        """
        주소를 좌표로 변환한다.

        현재 구현은 OpenStreetMap Nominatim 공개 검색 API를 사용한다.
        API 키는 필요 없지만 인터넷 연결이 필요하고, 너무 잦은 반복 호출은 피해야 한다.
        """
        try:
            url = "https://nominatim.openstreetmap.org/search"
            params = {
                "q": address,
                "format": "json",
                "limit": 1,
                "countrycodes": "kr",
                "addressdetails": 1,
                "accept-language": "ko"
            }
            headers = {
                "User-Agent": "FLYHIGH-Drone-GCS/1.0"
            }

            r = requests.get(url, params=params, headers=headers, timeout=12)
            r.raise_for_status()
            data = r.json()

            if not data:
                raise ValueError("주소 검색 결과가 없습니다. 주소를 더 구체적으로 입력하세요.")

            item = data[0]
            lat = float(item["lat"])
            lon = float(item["lon"])
            display_name = item.get("display_name", address)

            self.after(0, lambda: self.apply_geocoded_target(lat, lon, display_name))

        except Exception as e:
            err = str(e)
            self.after(
                0,
                lambda: self.write_result(
                    "주소 변환 실패\n\n"
                    f"입력 주소: {address}\n"
                    f"오류: {err}\n\n"
                    "해결 방법:\n"
                    "1. 인터넷 연결을 확인하세요.\n"
                    "2. 주소를 더 구체적으로 입력하세요.\n"
                    "3. 구글맵에서 위치를 우클릭해 위도/경도를 직접 복사해 넣어도 됩니다."
                )
            )

    def apply_geocoded_target(self, lat, lon, display_name):
        """변환된 좌표를 입력칸, 지도 TARGET, 해설 패널에 반영한다."""
        try:
            self.lat_entry.delete(0, tk.END)
            self.lat_entry.insert(0, f"{lat:.6f}")
            self.lon_entry.delete(0, tk.END)
            self.lon_entry.insert(0, f"{lon:.6f}")

            self.parent.set_ai_target_from_address(lat, lon, display_name)

            self.write_result(
                "주소 변환 완료\n\n"
                f"검색 결과: {display_name}\n\n"
                f"목표 위도: {lat:.6f}\n"
                f"목표 경도: {lon:.6f}\n\n"
                "이제 Ollama 분석 또는 규칙 기반 분석을 누르면 이 좌표를 기준으로 안전고도와 배터리 효율 점수를 계산합니다."
            )

        except Exception as e:
            messagebox.showwarning("좌표 반영 오류", str(e))

    def write_result(self, text):
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, text)
        self.result_text.config(state="disabled")

    def read_common_inputs(self):
        user_prompt = self.prompt_text.get("1.0", tk.END).strip()
        model = self.model_entry.get().strip() or "llama3.2"
        try:
            lat = float(self.lat_entry.get().strip())
            lon = float(self.lon_entry.get().strip())
        except ValueError:
            raise ValueError(
                "목표 위도/경도는 숫자여야 합니다.\n"
                "주소를 붙여넣은 경우 [주소 → 좌표] 버튼을 먼저 눌러 좌표로 변환하세요."
            )
        margin = float(self.margin_entry.get().strip())
        max_alt = float(self.max_alt_entry.get().strip())
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("목표 좌표 범위가 올바르지 않습니다.")
        if margin < 0 or max_alt <= 0:
            raise ValueError("안전여유와 최대고도 값을 확인하세요.")
        return user_prompt, model, lat, lon, margin, max_alt

    def on_analyze_ollama(self):
        try:
            user_prompt, model, lat, lon, margin, max_alt = self.read_common_inputs()
        except Exception as e:
            messagebox.showwarning("입력 오류", str(e))
            return

        self.write_result("Ollama 분석 중...\nlocalhost:11434 연결을 시도합니다.")
        threading.Thread(
            target=self._analyze_worker,
            args=(user_prompt, model, lat, lon, margin, max_alt, True),
            daemon=True
        ).start()

    def on_analyze_rule(self):
        try:
            user_prompt, model, lat, lon, margin, max_alt = self.read_common_inputs()
            plan = self.parent.build_ai_route_plan(
                user_prompt=user_prompt,
                target_lat=lat,
                target_lon=lon,
                model=model,
                safety_margin_m=margin,
                max_altitude_m=max_alt,
                use_ollama=False
            )
            self.parent.ai_route_plan = plan
            self.write_result(self.parent.format_ai_route_plan(plan))
            self.parent.show_ai_route_plan(plan)
        except Exception as e:
            messagebox.showwarning("분석 오류", str(e))

    def _analyze_worker(self, user_prompt, model, lat, lon, margin, max_alt, use_ollama):
        try:
            plan = self.parent.build_ai_route_plan(
                user_prompt=user_prompt,
                target_lat=lat,
                target_lon=lon,
                model=model,
                safety_margin_m=margin,
                max_altitude_m=max_alt,
                use_ollama=use_ollama
            )
            self.parent.ai_route_plan = plan
            text = self.parent.format_ai_route_plan(plan)
            self.after(0, lambda: self.write_result(text))
            self.after(0, lambda: self.parent.show_ai_route_plan(plan))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.write_result(f"Ollama 분석 실패 또는 계산 오류:\n{err}\n\nOllama가 꺼져 있으면 '규칙 기반 분석'을 사용하세요."))
            self.after(0, lambda: self.parent.explain_failure(
                "AI Route Assist 분석 실패",
                "OLLAMA_API / RULE_ENGINE",
                err,
                "1. Ollama가 설치되어 있는지 확인하세요.\n2. Windows에서는 Ollama 앱이 실행 중인지 확인하세요.\n3. 터미널에서 ollama run llama3.2 를 먼저 실행해 모델을 받아두세요.\n4. 또는 규칙 기반 분석 버튼을 사용하세요.",
                "AI_ROUTE_ANALYSIS"
            ))

    def on_execute(self):
        try:
            if self.parent.ai_route_plan is None:
                self.on_analyze_rule()
            self.parent.execute_ai_route_plan(self.parent.ai_route_plan)
        except Exception as e:
            messagebox.showwarning("실행 오류", str(e))


# ===============================================================
# 6-0. 초보자 튜토리얼 팝업 클래스
# ===============================================================
class BeginnerTutorialDialog(tk.Toplevel):
    """초보자를 위한 단계별 튜토리얼 오버레이"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.idx = 0
        self.title("Beginner Tutorial")
        self.geometry("520x360")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.transient(parent)
        self.configure(bg="#07111f")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.steps = [
            {"title": "1. Start SITL", "target": "상단 Start SITL 버튼", "body": "시뮬레이터를 먼저 실행합니다. 이 단계가 완료되어야 MAVLink 연결과 GPS 데이터가 들어옵니다."},
            {"title": "2. 비행 모드 선택", "target": "오른쪽 01 FLIGHT MODE", "body": "GUIDED는 지도 클릭 이동과 AI Route 실행에 적합합니다. LOITER/ALT_HOLD는 조이스틱 실습에 적합합니다."},
            {"title": "3. ARM / TAKEOFF", "target": "오른쪽 02 VEHICLE CONTROL", "body": "ARM은 모터 시동 가능 상태입니다. 이륙 전에는 배터리 기준도 확인합니다. 본 드론 프로파일은 이륙 전 70% 이상을 권장합니다."},
            {"title": "4. 지도 클릭과 Waypoint 기록", "target": "지도 영역 + 하단 WAYPOINT LOG", "body": "지도를 클릭하면 목표 좌표가 TARGET으로 기록됩니다. 좌표, 명령, 고도, 실행 상태는 하단 Waypoint Log와 CSV에 남습니다."},
            {"title": "5. AI Route / Battery Safe", "target": "Preset 2, Preset 3", "body": "AI Route는 주소/자연어 기반으로 안전 고도를 추천합니다. Battery Safe는 현재 배터리로 목표 이동과 HOME 복귀가 가능한지 검증합니다."},
            {"title": "6. 고도 기준", "target": "COMMAND EXPLAIN", "body": "이 프로그램에서 AI 추천 고도와 Guided 이동 고도는 HOME 기준 상대고도(AGL)입니다. 절대고도/해발고도(AMSL)가 아닙니다."},
        ]

        tk.Label(self, text="Beginner Flight Tutorial", bg="#07111f", fg="#7CFF6B", font=("맑은 고딕", 16, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        self.step_label = tk.Label(self, text="", bg="#07111f", fg="#ffd166", font=("맑은 고딕", 12, "bold"), anchor="w")
        self.step_label.pack(fill="x", padx=18, pady=(8, 2))
        self.target_label = tk.Label(self, text="", bg="#132235", fg="#00ffcc", font=("맑은 고딕", 10, "bold"), anchor="w", padx=10, pady=8)
        self.target_label.pack(fill="x", padx=18, pady=8)
        self.body_label = tk.Label(self, text="", bg="#07111f", fg="#eaf6ff", font=("맑은 고딕", 10), justify="left", wraplength=470, anchor="nw")
        self.body_label.pack(fill="both", expand=True, padx=18, pady=8)

        btn_row = tk.Frame(self, bg="#07111f")
        btn_row.pack(fill="x", padx=18, pady=(4, 16))
        tk.Button(btn_row, text="이전", command=self.prev_step, bg="#203149", fg="white", relief="flat", width=10).pack(side="left")
        tk.Button(btn_row, text="다음", command=self.next_step, bg="#7CFF6B", fg="#07111f", relief="flat", width=10).pack(side="left", padx=8)
        tk.Button(btn_row, text="닫기", command=self.destroy, bg="#ff4d6d", fg="white", relief="flat", width=10).pack(side="right")
        self.render_step()

    def render_step(self):
        step = self.steps[self.idx]
        self.step_label.config(text=f"{self.idx + 1} / {len(self.steps)}   {step['title']}")
        self.target_label.config(text=f"강조 위치: {step['target']}")
        self.body_label.config(text=step["body"])
        try:
            self.parent.update_command_explain(f"튜토리얼 - {step['title']}", "UI_TUTORIAL", f"- 강조 위치: {step['target']}\n- 초보자 실습 단계 안내", step["body"])
            self.parent.set_status(f"튜토리얼 진행 중: {step['title']}")
        except Exception:
            pass

    def next_step(self):
        if self.idx < len(self.steps) - 1:
            self.idx += 1
            self.render_step()
        else:
            self.destroy()

    def prev_step(self):
        if self.idx > 0:
            self.idx -= 1
            self.render_step()



# ===============================================================
# 6-1. Preset 3 - Battery Safe 팝업 클래스
# ===============================================================
class BatterySafeDialog(tk.Toplevel):
    """배터리 기반 경로 실행 가능성 검증 팝업"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Preset 3 - Battery Safe Route Check")
        self.geometry("700x640")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.transient(parent)
        self.configure(bg="#0b1320")

        title = tk.Label(
            self,
            text="Battery Safe Route Mode",
            bg="#0b1320",
            fg="#ffb703",
            font=("맑은 고딕", 17, "bold")
        )
        title.pack(anchor="w", padx=18, pady=(14, 0))

        sub = tk.Label(
            self,
            text="Preset 3은 AI Route 계획을 현재 배터리, 목표 거리, 복귀 거리, 상승 고도, 예비 배터리 기준으로 검증합니다. 실제 배터리 소모를 보장하는 기능이 아니라 교육용 추정 기능입니다.",
            bg="#0b1320",
            fg="#b8c7d9",
            font=("맑은 고딕", 9),
            wraplength=650,
            justify="left"
        )
        sub.pack(anchor="w", padx=18, pady=(2, 10))

        form = tk.Frame(self, bg="#132235", highlightthickness=1, highlightbackground="#ffb703")
        form.pack(fill="x", padx=18, pady=8)

        current_bat = parent.d_bat_p if parent.d_bat_p and parent.d_bat_p > 0 else 50

        row = tk.Frame(form, bg="#132235")
        row.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(row, text="현재 배터리(%)", bg="#132235", fg="#eaf6ff", width=15, anchor="w").pack(side="left")
        self.battery_entry = tk.Entry(row, width=10, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.battery_entry.insert(0, str(int(current_bat)))
        self.battery_entry.pack(side="left", padx=(0, 12))

        tk.Label(row, text="예비 배터리(%)", bg="#132235", fg="#eaf6ff", width=15, anchor="w").pack(side="left")
        self.reserve_entry = tk.Entry(row, width=10, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.reserve_entry.insert(0, "20")
        self.reserve_entry.pack(side="left")

        row2 = tk.Frame(form, bg="#132235")
        row2.pack(fill="x", padx=10, pady=4)
        tk.Label(row2, text="고도 여유(m)", bg="#132235", fg="#eaf6ff", width=15, anchor="w").pack(side="left")
        self.alt_buffer_entry = tk.Entry(row2, width=10, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.alt_buffer_entry.insert(0, "5")
        self.alt_buffer_entry.pack(side="left", padx=(0, 12))

        tk.Label(row2, text="호버링 여유(%)", bg="#132235", fg="#eaf6ff", width=15, anchor="w").pack(side="left")
        self.hover_entry = tk.Entry(row2, width=10, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.hover_entry.insert(0, str(int(parent.drone_profile.get("operation_margin_pct", 4))))
        self.hover_entry.pack(side="left")

        row3 = tk.Frame(form, bg="#132235")
        row3.pack(fill="x", padx=10, pady=4)
        tk.Label(row3, text="최소 이륙 배터리(%)", bg="#132235", fg="#eaf6ff", width=18, anchor="w").pack(side="left")
        self.min_takeoff_entry = tk.Entry(row3, width=10, bg="#0b1320", fg="#eaf6ff", insertbackground="white", relief="flat")
        self.min_takeoff_entry.insert(0, str(int(parent.drone_profile.get("min_takeoff_battery_pct", 70))))
        self.min_takeoff_entry.pack(side="left", padx=(0, 12))

        tk.Label(
            row3,
            text="※ 이륙 전 배터리 기준",
            bg="#132235",
            fg="#ffb703",
            font=("맑은 고딕", 8, "bold"),
            anchor="w"
        ).pack(side="left", fill="x", expand=True)

        help_label = tk.Label(
            form,
            text="※ AI Route 계획이 없으면 현재 TARGET/기본 좌표를 기준으로 임시 계획을 만든 뒤 배터리 검증을 수행합니다.",
            bg="#132235",
            fg="#94a9c4",
            font=("맑은 고딕", 8),
            anchor="w",
            justify="left"
        )
        help_label.pack(fill="x", padx=10, pady=(0, 8))

        btn_row = tk.Frame(self, bg="#0b1320")
        btn_row.pack(fill="x", padx=18, pady=8)

        tk.Button(
            btn_row,
            text="AI Route 계획 불러오기",
            command=self.load_or_create_plan,
            bg="#8e7dff",
            fg="white",
            relief="flat",
            font=("맑은 고딕", 10, "bold"),
            height=2
        ).pack(side="left", fill="x", expand=True, padx=(0, 5))

        tk.Button(
            btn_row,
            text="배터리 검증",
            command=self.on_check_battery,
            bg="#ffb703",
            fg="#101923",
            relief="flat",
            font=("맑은 고딕", 10, "bold"),
            height=2
        ).pack(side="left", fill="x", expand=True, padx=5)

        tk.Button(
            btn_row,
            text="검증 후 실행",
            command=self.on_execute_if_safe,
            bg="#2ecc71",
            fg="#06111f",
            relief="flat",
            font=("맑은 고딕", 10, "bold"),
            height=2
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        self.result_text = tk.Text(
            self,
            height=20,
            bg="#07111f",
            fg="#eaf6ff",
            insertbackground="white",
            font=("Consolas", 10),
            relief="flat"
        )
        self.result_text.pack(fill="both", expand=True, padx=18, pady=(6, 12))

        self.write_result(
            "Battery Safe Check 대기 중입니다.\n\n"
            "1) 먼저 Preset 2에서 AI Route 계획을 생성하거나 TARGET을 지정하세요.\n"
            "2) 현재 배터리와 예비 배터리 기준을 입력하세요.\n"
            "3) [배터리 검증]을 누르면 목표 이동 + HOME 복귀까지 고려해 실행 가능성을 추정합니다.\n\n"
            "판정 기준:\n"
            "- SAFE: 복귀 후 예비 배터리 + 10% 이상 여유\n"
            "- CAUTION: 예비 배터리는 남지만 여유가 작음\n"
            "- NOT_RECOMMENDED: 예비 배터리 기준 미달"
        )

    def write_result(self, text):
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", text)
        self.result_text.config(state="disabled")

    def read_options(self):
        try:
            battery_pct = float(self.battery_entry.get().strip())
            reserve_pct = float(self.reserve_entry.get().strip())
            alt_buffer_m = float(self.alt_buffer_entry.get().strip())
            hover_pct = float(self.hover_entry.get().strip())
            min_takeoff_pct = float(self.min_takeoff_entry.get().strip())
        except ValueError:
            raise ValueError("배터리/예비/고도/호버링/최소 이륙 배터리 값은 숫자로 입력해야 합니다.")

        if not (0 <= battery_pct <= 100):
            raise ValueError("현재 배터리는 0~100 사이여야 합니다.")
        if not (0 <= reserve_pct <= 80):
            raise ValueError("예비 배터리는 0~80 사이가 적절합니다.")
        if not (0 <= min_takeoff_pct <= 100):
            raise ValueError("최소 이륙 배터리는 0~100 사이여야 합니다.")
        if alt_buffer_m < 0:
            raise ValueError("고도 여유는 0 이상이어야 합니다.")
        if hover_pct < 0:
            raise ValueError("호버링 여유는 0 이상이어야 합니다.")

        return battery_pct, reserve_pct, alt_buffer_m, hover_pct, min_takeoff_pct

    def load_or_create_plan(self):
        try:
            plan = self.parent.ensure_ai_route_plan_for_battery_safe()
            self.parent.ai_route_plan = plan
            self.write_result(
                "AI Route 계획을 불러왔습니다.\n\n"
                + self.parent.format_ai_route_plan(plan)
                + "\n\n이제 [배터리 검증]을 누르세요."
            )
            self.parent.show_ai_route_plan(plan)
        except Exception as e:
            messagebox.showwarning("계획 불러오기 오류", str(e))

    def on_check_battery(self):
        try:
            plan = self.parent.ensure_ai_route_plan_for_battery_safe()
            battery_pct, reserve_pct, alt_buffer_m, hover_pct, min_takeoff_pct = self.read_options()

            check = self.parent.build_battery_feasibility_check(
                plan,
                battery_pct=battery_pct,
                reserve_pct=reserve_pct,
                alt_buffer_m=alt_buffer_m,
                hover_pct=hover_pct,
                min_takeoff_pct=min_takeoff_pct
            )
            self.parent.last_battery_check = check
            self.parent.show_battery_feasibility_check(check)
            self.write_result(self.parent.format_battery_feasibility_check(check))
        except Exception as e:
            messagebox.showwarning("배터리 검증 오류", str(e))

    def on_execute_if_safe(self):
        try:
            plan = self.parent.ensure_ai_route_plan_for_battery_safe()
            battery_pct, reserve_pct, alt_buffer_m, hover_pct, min_takeoff_pct = self.read_options()

            check = self.parent.build_battery_feasibility_check(
                plan,
                battery_pct=battery_pct,
                reserve_pct=reserve_pct,
                alt_buffer_m=alt_buffer_m,
                hover_pct=hover_pct,
                min_takeoff_pct=min_takeoff_pct
            )
            self.parent.last_battery_check = check
            self.parent.show_battery_feasibility_check(check)
            self.write_result(self.parent.format_battery_feasibility_check(check))

            verdict = check.get("verdict")
            if verdict == "NOT_RECOMMENDED":
                messagebox.showwarning(
                    "Battery Safe 실행 차단",
                    "현재 배터리 기준으로 목표 이동 후 HOME 복귀 시 예비 배터리 기준을 만족하기 어렵습니다.\n\n결과 내용을 확인하세요."
                )
                self.parent.add_practice_log(
                    "BATTERY_SAFE_EXECUTE",
                    "BATTERY_FEASIBILITY_GATE",
                    "BLOCKED",
                    f"verdict={verdict}, estimated_total_pct={check.get('estimated_total_pct')}",
                    "FAIL"
                )
                return

            if verdict == "CAUTION":
                ok = messagebox.askyesno(
                    "Battery Safe 주의",
                    "예비 배터리는 남지만 여유가 크지 않습니다.\n그래도 추천 고도 실행을 진행할까요?"
                )
                if not ok:
                    return

            self.parent.execute_ai_route_plan_with_battery_check(plan, check)

        except Exception as e:
            messagebox.showwarning("Battery Safe 실행 오류", str(e))



# ===============================================================
# 7. GUI + GCS 메인 클래스
# ===============================================================
class GCSMap(tk.Tk):
    def __init__(self):
        super().__init__()

        # 이미지 참조 변수들을 먼저 선언
        self.window_icon_img = None
        self.drone_raw_img = None
        self.home_icon = None
        self.target_icon = None
        self.drone_icon = None
        self.drone_rotated_icon = None

        self.load_images()

        self.withdraw()

        selector = MapSelectionDialog(self)
        self.wait_window(selector)

        if not selector.result:
            sys.exit(0)

        self.selected_map_url, self.selected_db_name, self.selected_max_zoom = selector.result
        self.available_map_options = self.get_map_options()
        self.selected_map_label = self.find_map_label_by_url(self.selected_map_url)
        self.deiconify()

        self.title("FLYHIGH Drone GCS (초보자용 AI 드론 지상관제 교육 프로그램)")
        self.geometry("1450x850")

        self.sitl = SITLManager()
        self.drone = None
        self.mav_lock = threading.Lock()

        self.drone_marker = None
        self.home_marker = None
        self.target_marker = None
        self.path_line = None

        # 실제 실행 중인 이동 경로 표시용 변수
        # target_marker/path_line은 사용자가 새로 클릭한 임시 목표에도 바뀔 수 있으므로,
        # 실제 이동 명령이 전송된 목적지와 경로선은 별도로 보관한다.
        self.active_route_marker = None
        self.active_route_line = None
        self.active_route_target = None  # (lat, lon, alt)
        self.active_route_label = ""

        self.last_home_lat = 37.565575
        self.last_home_lon = 126.977987

        self.running = True
        self.is_closing = False
        self.starting = False
        self.stop_event = threading.Event()

        self.init_msg_sent = False
        self.gps_enable_time = None
        self.after_id = None
        self.countdown_popup = None

        self.last_mode = None
        self.is_ready_to_arm = False
        self.prearm_msg = "점검 중"

        self.d_roll = 0.0
        self.d_pitch = 0.0
        self.d_alt = 0.0
        self.d_speed = 0.0
        self.d_mode = "DISCONN"
        self.d_sats = 0
        self.d_bat_v = 0.0
        self.d_bat_p = 0
        self.d_is_armed = False
        self.d_lat = 0.0
        self.d_lon = 0.0
        self.d_yaw = 0.0
        self.last_drone_yaw = -1.0

        self.disarm_time = None
        self.last_map_center_update_time = 0.0

        # 조이스틱 제어 변수 및 PWM 기본값
        self.joystick_visible = tk.BooleanVar(value=False)
        self.js_roll = 1500
        self.js_pitch = 1500
        self.js_throttle = 1500
        self.js_yaw = 1500

        # 지도 중심 추적 방식: 0=자동 이동 없음, 1=홈 마커 중심, 2=드론 중앙 추적
        self.map_center_mode = tk.IntVar(value=1)
        self.last_center_mode = 1

        self.force_ui_armed = False

        # 실습 리포트 자동 생성용 상태 변수
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_start_ts = time.time()
        self.practice_logs = []
        self.report_saved = False
        self.last_report_path = None

        # 프리셋 / AI 주행 보조용 상태 변수
        # 1 = 초보자 학습 모드, 2 = AI Route Assist 모드
        self.control_preset = tk.IntVar(value=1)
        self.ai_route_plan = None
        self.ai_route_dialog = None
        self.battery_safe_dialog = None
        self.last_battery_check = None

        # 드론 특성 기반 배터리 판단 프로파일
        # 실제 배터리 물리 모델이 아니라 실습/교육용 추정 프로파일이다.
        self.drone_profile = {
            "name": "Training Quad",
            "min_takeoff_battery_pct": 70.0,
            "reserve_battery_pct": 20.0,
            "horizontal_pct_per_100m": 1.20,
            "climb_pct_per_10m": 1.60,
            "descent_pct_per_10m": 0.45,
            "operation_margin_pct": 4.0
        }

        # Waypoint 기록용 데이터
        self.waypoint_records = []
        self.waypoint_seq = 0
        self.waypoint_panel_expanded = False
        self.waypoint_panel_visible = False
        self.last_waypoint_report_path = None

        # 실제 건물 DB/API 연동 전까지 사용하는 실습용 샘플 건물 데이터
        # 좌표는 기본 HOME 주변의 예시값이며, 높이는 m 단위다.
        self.sample_buildings = [
            {"name": "Sample Building A", "lat": 37.565850, "lon": 126.978250, "height_m": 28},
            {"name": "Sample Building B", "lat": 37.566120, "lon": 126.978600, "height_m": 45},
            {"name": "Sample Building C", "lat": 37.565300, "lon": 126.977600, "height_m": 18},
            {"name": "Sample Building D", "lat": 37.566350, "lon": 126.977900, "height_m": 36},
            {"name": "Sample Building E", "lat": 37.564980, "lon": 126.978450, "height_m": 22},
        ]

        self.status_var = tk.StringVar(value="SITL 실행 대기 중...")
        self.status_bar = tk.Label(
            self,
            textvariable=self.status_var,
            bd=1,
            relief="sunken",
            anchor="w",
            font=("맑은 고딕", 12)
        )
        self.status_bar.pack(side="bottom", fill="x")

        map_db_dir = os.path.join(current_dir, "map")
        if not os.path.exists(map_db_dir):
            os.makedirs(map_db_dir)
            print(f"폴더가 없어서 생성했습니다: {map_db_dir}")

        db_path = os.path.join(map_db_dir, self.selected_db_name)
        self.init_sqlite_db(db_path)

        self.left_container = tk.Frame(self)
        self.left_container.pack(side="left", fill="both", expand=True)

        self.right_panel = tk.Frame(self, width=360, bg="#101923")
        self.right_panel.pack(side="right", fill="y")
        self.right_panel.pack_propagate(False)

        self.create_right_control_panel()

        self.map_widget = tkintermapview.TkinterMapView(
            self.left_container,
            width=800,
            height=600,
            corner_radius=0,
            database_path=db_path,
            use_database_only=False
        )
        self.map_widget.pack(fill="both", expand=True)

        self.map_widget.set_tile_server(
            self.selected_map_url,
            max_zoom=self.selected_max_zoom
        )

        # 지도 왼쪽 마우스 클릭 이벤트 연결
        self.map_widget.add_left_click_map_command(self.on_map_left_click)

        # 조이스틱 패널의 부모 위젯은 지도 위젯으로 지정
        self.js_panel_frame = tk.Frame(self.map_widget, bg="#2c3e50", bd=1, relief="solid")
        self.create_virtual_joystick()

        self.zoom_label = tk.Label(
            self.map_widget,
            text="Zoom: --",
            font=("맑은 고딕", 11, "bold"),
            fg="blue",
            bg="white",
            relief="solid",
            bd=1
        )
        self.zoom_label.place(relx=0.0, y=100, x=10, anchor="nw")

        self.hud = GCSHUD(self.map_widget)
        self.hud.place(relx=1.0, rely=0, x=-10, y=10, anchor="ne")

        # 왼쪽 하단 대형 명령 해설 패널 생성
        self.create_command_explain_panel()

        # 하단 Waypoint 기록 패널 생성
        self.create_waypoint_panel()

        def init_map():
            self.map_widget.set_position(self.last_home_lat, self.last_home_lon)
            self.map_widget.set_zoom(17)

        self.after(100, init_map)

        panel = tk.Frame(self.map_widget)
        panel.place(relx=0.5, y=10, anchor="n")

        tk.Label(panel, text="LAT :", font=("맑은 고딕", 12, "bold"), width=5).grid(row=0, column=0)
        self.lat_entry = tk.Entry(panel, width=15)
        self.lat_entry.insert(0, str(self.last_home_lat))
        self.lat_entry.grid(row=0, column=1)

        tk.Label(panel, text="LON :", font=("맑은 고딕", 12, "bold"), width=5).grid(row=0, column=2, padx=(10, 0))
        self.lon_entry = tk.Entry(panel, width=15)
        self.lon_entry.insert(0, str(self.last_home_lon))
        self.lon_entry.grid(row=0, column=3)

        self.start_btn = tk.Button(
            panel,
            text="Start SITL",
            font=("맑은 고딕", 12, "bold"),
            command=self.toggle_sitl,
            bg="yellow",
            activebackground="orange"
        )
        self.start_btn.grid(row=0, column=4, padx=(10, 0))

        atexit.register(self.cleanup)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        sys.excepthook = self.handle_crash

        self.mav_thread = threading.Thread(target=self.mavlink_loop, daemon=True)
        self.mav_thread.start()

        # 조이스틱 RC override 송신 루프 시작
        self.js_loop()

        self.update_drone_data()

    # -----------------------------------------------------------
    # 이미지 로딩
    # -----------------------------------------------------------
    def load_images(self):
        try:
            win_icon_path = os.path.join(current_dir, "logo_icon.png")
            if os.path.exists(win_icon_path):
                self.window_icon_img = ImageTk.PhotoImage(Image.open(win_icon_path))
                self.iconphoto(True, self.window_icon_img)  # type: ignore[arg-type]

            drone_img_path = os.path.join(current_dir, "drone.png")
            if os.path.exists(drone_img_path):
                self.drone_raw_img = Image.open(drone_img_path).convert("RGBA").resize((100, 100))

            home_img_path = os.path.join(current_dir, "home.png")
            if os.path.exists(home_img_path):
                self.home_icon = ImageTk.PhotoImage(Image.open(home_img_path).resize((45, 45)))

            target_img_path = os.path.join(current_dir, "target.png")
            if os.path.exists(target_img_path):
                self.target_icon = ImageTk.PhotoImage(Image.open(target_img_path).resize((45, 45)))

        except Exception as e:
            print(f"[IMAGE LOAD ERROR] 이미지를 아이콘으로 설정할 수 없습니다: {e}")

    # -----------------------------------------------------------
    # 지도 스타일 관리
    # -----------------------------------------------------------
    def get_map_options(self):
        """시작 시 선택 / 실행 중 변경에 공통으로 사용하는 지도 옵션"""
        return {
            "1. openstreetmap": (
                "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "openstreetmap.db",
                19
            ),
            "2. basemaps.cartocdn": (
                "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
                "carto_light.db",
                19
            ),
            "3. basemaps.cartocdn dark": (
                "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
                "carto_dark.db",
                19
            ),
            "4. opentopomap": (
                "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
                "opentopo.db",
                17
            ),
            "5. Esri Satellite": (
                "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                "esri_sat.db",
                19
            ),
            "6. Google Hybrid": (
                "https://mt0.google.com/vt/lyrs=y&hl=en&x={x}&y={y}&z={z}",
                "google_hybrid.db",
                19
            )
        }

    def find_map_label_by_url(self, url):
        """타일 서버 URL 기준으로 현재 지도 이름 찾기"""
        try:
            for label, option in self.get_map_options().items():
                if option[0] == url:
                    return label
        except Exception:
            pass

        return "1. openstreetmap"

    def apply_runtime_map_style(self):
        """
        실행 중 지도 스타일 변경.

        주의:
        - SITL, MAVLink 연결, HOME/DRONE/TARGET 마커, 경로선은 초기화하지 않는다.
        - 지도 타일 서버만 교체한다.
        - database_path 자체는 TkinterMapView 생성 시 정해지므로 실행 중에는 바꾸지 않는다.
          대신 같은 DB 내부에서 서버 URL별로 타일 캐시가 분리되어 저장된다.
        """
        try:
            selected = self.map_style_var.get()
        except Exception:
            selected = getattr(self, "selected_map_label", "1. openstreetmap")

        options = self.get_map_options()

        if selected not in options:
            self.set_status("지도 변경 실패: 선택한 지도 옵션을 찾을 수 없습니다.")
            return

        tile_url, db_name, max_zoom = options[selected]

        try:
            current_zoom = int(round(getattr(self.map_widget, "zoom", 17)))
        except Exception:
            current_zoom = 17

        try:
            self.map_widget.set_tile_server(tile_url, max_zoom=max_zoom)

            if current_zoom > max_zoom:
                self.map_widget.set_zoom(max_zoom)

            self.selected_map_url = tile_url
            self.selected_db_name = db_name
            self.selected_max_zoom = max_zoom
            self.selected_map_label = selected

            detail = (
                f"지도 스타일 변경: {selected}, "
                f"max_zoom={max_zoom}, "
                f"기존 비행 상태와 마커는 유지"
            )

            self.set_status(f"지도 변경 완료: {selected}")
            self.update_command_explain(
                "지도 스타일 변경",
                "TkinterMapView.set_tile_server",
                "- 실행 중 지도 타일 서버만 변경\n"
                "- SITL / MAVLink 연결 유지\n"
                "- HOME / DRONE / TARGET / 경로선 유지\n"
                "- 현재 지도 DB 파일은 초기 실행 시 선택된 DB를 계속 사용",
                detail
            )
            self.add_practice_log(
                "MAP_STYLE_CHANGE",
                "UI_MAP_SWITCH",
                "CHANGED",
                detail
            )

        except Exception as e:
            self.set_status(f"지도 변경 오류: {e}")
            self.update_command_explain(
                "지도 변경 실패",
                "TkinterMapView.set_tile_server",
                "- 지도 위젯이 정상 생성되어 있어야 합니다.\n"
                "- 인터넷 연결 또는 타일 서버 URL을 확인해야 합니다.",
                f"오류: {e}"
            )
            print("[MAP SWITCH ERROR]", e)


    # -----------------------------------------------------------
    # DB 초기화
    # -----------------------------------------------------------
    def init_sqlite_db(self, path):
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tile_data "
                "(server TEXT, zoom INTEGER, x INTEGER, y INTEGER, image BLOB, "
                "PRIMARY KEY (server, zoom, x, y))"
            )
            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[DB INIT ERROR] {e}")

    # -----------------------------------------------------------
    # 오른쪽 제어 패널 UI
    # -----------------------------------------------------------
    def create_right_control_panel(self):
        """오른쪽 제어 패널 UI를 교육용 GCS 스타일로 재구성"""
        panel_bg = "#101923"
        card_bg = "#172435"
        line_cyan = "#00d4ff"
        line_green = "#20e3b2"
        text_main = "#eaf6ff"
        text_sub = "#9fb3c8"
        warn_yellow = "#ffd166"

        self.right_panel.configure(bg=panel_bg)

        # 기존 오른쪽 패널 내부 위젯 제거
        for child in self.right_panel.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

        # 오른쪽 패널 스크롤 영역 생성
        self.right_scroll_canvas = tk.Canvas(
            self.right_panel,
            bg=panel_bg,
            highlightthickness=0,
            bd=0
        )
        self.right_scrollbar = tk.Scrollbar(
            self.right_panel,
            orient="vertical",
            command=self.right_scroll_canvas.yview
        )
        self.right_scroll_canvas.configure(yscrollcommand=self.right_scrollbar.set)

        self.right_scrollbar.pack(side="right", fill="y")
        self.right_scroll_canvas.pack(side="left", fill="both", expand=True)

        self.right_scroll_content = tk.Frame(self.right_scroll_canvas, bg=panel_bg)
        self.right_scroll_window = self.right_scroll_canvas.create_window(
            (0, 0),
            window=self.right_scroll_content,
            anchor="nw"
        )

        def _sync_right_scroll_region(_event=None):
            try:
                self.right_scroll_canvas.configure(
                    scrollregion=self.right_scroll_canvas.bbox("all")
                )
            except Exception:
                pass

        def _sync_right_content_width(event):
            try:
                self.right_scroll_canvas.itemconfigure(
                    self.right_scroll_window,
                    width=event.width
                )
            except Exception:
                pass

        def _on_right_mousewheel(event):
            try:
                if getattr(self, "is_closing", False):
                    return
                # Windows/macOS MouseWheel
                if hasattr(event, "delta") and event.delta:
                    self.right_scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        def _on_right_linux_scroll_up(_event):
            try:
                self.right_scroll_canvas.yview_scroll(-3, "units")
            except Exception:
                pass

        def _on_right_linux_scroll_down(_event):
            try:
                self.right_scroll_canvas.yview_scroll(3, "units")
            except Exception:
                pass

        def _bind_right_wheel(_event):
            try:
                self.bind_all("<MouseWheel>", _on_right_mousewheel)
                self.bind_all("<Button-4>", _on_right_linux_scroll_up)
                self.bind_all("<Button-5>", _on_right_linux_scroll_down)
            except Exception:
                pass

        def _unbind_right_wheel(_event):
            try:
                self.unbind_all("<MouseWheel>")
                self.unbind_all("<Button-4>")
                self.unbind_all("<Button-5>")
            except Exception:
                pass

        self.right_scroll_content.bind("<Configure>", _sync_right_scroll_region)
        self.right_scroll_canvas.bind("<Configure>", _sync_right_content_width)
        self.right_scroll_canvas.bind("<Enter>", _bind_right_wheel)
        self.right_scroll_canvas.bind("<Leave>", _unbind_right_wheel)

        right_parent = self.right_scroll_content

        # 상단 타이틀 영역
        title_box = tk.Frame(right_parent, bg=panel_bg)
        title_box.pack(fill="x", padx=12, pady=(12, 8))

        tk.Label(
            title_box,
            text="FLYHIGH Drone GCS ",
            bg=panel_bg,
            fg=line_cyan,
            font=("맑은 고딕", 16, "bold")
        ).pack(anchor="w")

        tk.Label(
            title_box,
            text="(초보자용 AI 드론 지상관제 교육 프로그램)",
            bg=panel_bg,
            fg=text_sub,
            font=("맑은 고딕", 9)
        ).pack(anchor="w", pady=(0, 4))

        tk.Frame(title_box, bg=line_cyan, height=2).pack(fill="x", pady=(4, 0))

        def make_preset_button(parent, text, value, accent, command):
            return tk.Radiobutton(
                parent,
                text=text,
                variable=self.control_preset,
                value=value,
                command=command,
                indicatoron=False,
                bg="#18283b",
                fg=text_main,
                selectcolor=accent,
                activebackground=accent,
                activeforeground="white",
                relief="flat",
                bd=0,
                height=2,
                font=("맑은 고딕", 9, "bold"),
                cursor="hand2"
            )

        preset_box = tk.Frame(right_parent, bg=panel_bg)
        preset_box.pack(fill="x", padx=12, pady=(0, 8))
        make_preset_button(
            preset_box,
            "Preset 1\n초보자 학습",
            1,
            "#7CFF6B",
            self.activate_beginner_preset
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))
        make_preset_button(
            preset_box,
            "Preset 2\nAI Route",
            2,
            "#8e7dff",
            self.activate_ai_route_preset
        ).pack(side="left", fill="x", expand=True, padx=3)
        make_preset_button(
            preset_box,
            "Preset 3\nBattery Safe",
            3,
            "#ffb703",
            self.activate_battery_safe_preset
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

        def make_section(parent, title, accent="#00d4ff"):
            outer = tk.Frame(parent, bg=accent, bd=0)
            outer.pack(fill="x", padx=12, pady=7)

            inner = tk.Frame(outer, bg=card_bg, bd=0)
            inner.pack(fill="x", padx=(3, 0), pady=0)

            tk.Label(
                inner,
                text=title,
                bg=card_bg,
                fg=text_main,
                font=("맑은 고딕", 10, "bold")
            ).pack(anchor="w", padx=10, pady=(8, 5))

            return inner

        def make_button(parent, text, command, bg="#26384d", fg="#eaf6ff", active="#2f4968", h=1):
            btn = tk.Button(
                parent,
                text=text,
                command=command,
                bg=bg,
                fg=fg,
                activebackground=active,
                activeforeground="white",
                relief="flat",
                bd=0,
                height=h,
                font=("맑은 고딕", 9, "bold"),
                cursor="hand2"
            )
            return btn

        # 초보자 단계별 비행 절차 안내 영역
        guide_frame = make_section(right_parent, "00  BEGINNER FLIGHT GUIDE", "#7CFF6B")

        self.beginner_guide_labels = {}
        self.beginner_guide_order = [
            ("sitl", "1. SITL 실행"),
            ("mavlink", "2. MAVLink 연결"),
            ("gps", "3. GPS 안정화"),
            ("mode", "4. 비행 모드 선택"),
            ("arm", "5. ARM / 시동"),
            ("takeoff", "6. TAKEOFF / 이륙"),
            ("control", "7. 이동 또는 조이스틱"),
            ("finish", "8. LAND/RTL 후 DISARM")
        ]

        for key, label_text in self.beginner_guide_order:
            row = tk.Label(
                guide_frame,
                text=f"□ {label_text}",
                bg="#0d1520",
                fg="#8395a7",
                anchor="w",
                justify="left",
                font=("맑은 고딕", 8, "bold"),
                padx=8,
                pady=3
            )
            row.pack(fill="x", padx=10, pady=1)
            self.beginner_guide_labels[key] = row

        self.beginner_recommend_label = tk.Label(
            guide_frame,
            text="권장 다음 단계: Start SITL을 눌러 시뮬레이터를 실행하세요.",
            bg="#101923",
            fg="#7CFF6B",
            anchor="w",
            justify="left",
            wraplength=300,
            font=("맑은 고딕", 8, "bold"),
            padx=8,
            pady=6
        )
        self.beginner_recommend_label.pack(fill="x", padx=10, pady=(5, 4))

        make_button(
            guide_frame,
            "튜토리얼 보기",
            self.open_beginner_tutorial,
            bg="#7CFF6B",
            fg="#07111f",
            active="#62d957"
        ).pack(fill="x", padx=10, pady=(0, 8))

        # 비행 모드 영역
        self.mode_frame = make_section(right_parent, "01  FLIGHT MODE", line_cyan)
        mode_grid = tk.Frame(self.mode_frame, bg=card_bg)
        mode_grid.pack(fill="x", padx=10, pady=(0, 8))

        modes = ["STABILIZE", "ALT_HOLD", "LOITER", "GUIDED", "AUTO", "RTL", "LAND"]
        for idx, m in enumerate(modes):
            grid_row = idx // 2
            grid_col = idx % 2
            btn = make_button(
                mode_grid,
                m,
                lambda mode_name=m: self.set_drone_mode(mode_name),
                bg="#203149" if m != "GUIDED" else "#0d6efd",
                active="#314966" if m != "GUIDED" else "#0b5ed7"
            )
            btn.grid(row=grid_row, column=grid_col, sticky="ew", padx=3, pady=3)

        mode_grid.grid_columnconfigure(0, weight=1)
        mode_grid.grid_columnconfigure(1, weight=1)

        # 기체 제어 영역
        self.arm_frame = make_section(right_parent, "02  VEHICLE CONTROL", line_green)

        make_button(
            self.arm_frame,
            "ARM / 시동",
            self.cmd_arm,
            bg="#ff8a00",
            fg="white",
            active="#cc6f00",
            h=1
        ).pack(fill="x", padx=10, pady=3)

        make_button(
            self.arm_frame,
            "DISARM / 해제",
            self.cmd_disarm,
            bg="#455a6f",
            fg="white",
            active="#33465a",
            h=1
        ).pack(fill="x", padx=10, pady=3)

        make_button(
            self.arm_frame,
            "TAKEOFF / 이륙",
            self.cmd_takeoff_popup,
            bg="#00a8e8",
            fg="white",
            active="#0077aa",
            h=1
        ).pack(fill="x", padx=10, pady=3)

        self.arm_status_label = tk.Label(
            self.arm_frame,
            text="상태: 연결 대기중",
            bg="#0f1b29",
            fg="#ecf0f1",
            font=("맑은 고딕", 10, "bold"),
            padx=8,
            pady=7
        )
        self.arm_status_label.pack(fill="x", padx=10, pady=(6, 10))

        # 기타 동작 영역
        self.etc_frame = make_section(right_parent, "03  TOOLS", warn_yellow)

        make_button(
            self.etc_frame,
            "AI Route Assist 열기",
            self.open_ai_route_assist_dialog,
            bg="#8e7dff",
            fg="white",
            active="#6f5cff"
        ).pack(fill="x", padx=10, pady=3)

        make_button(
            self.etc_frame,
            "Battery Safe Check 열기",
            self.open_battery_safe_dialog,
            bg="#ffb703",
            fg="#101923",
            active="#e09f00"
        ).pack(fill="x", padx=10, pady=3)

        make_button(
            self.etc_frame,
            "Waypoint 기록 보기/숨김",
            self.toggle_waypoint_panel,
            bg="#00ffcc",
            fg="#07111f",
            active="#00c9a7"
        ).pack(fill="x", padx=10, pady=3)

        make_button(
            self.etc_frame,
            "Waypoint CSV 저장",
            self.save_waypoint_report,
            bg="#2a9d8f",
            fg="white",
            active="#21867a"
        ).pack(fill="x", padx=10, pady=3)

        make_button(
            self.etc_frame,
            "배터리 잔량 초기화",
            self.cmd_battery_reset,
            bg="#ffd166",
            fg="#121212",
            active="#e6b94e"
        ).pack(fill="x", padx=10, pady=3)

        tool_row = tk.Frame(self.etc_frame, bg=card_bg)
        tool_row.pack(fill="x", padx=10, pady=(2, 5))

        self.js_check = tk.Checkbutton(
            self.etc_frame,
            text="가상 조이스틱 활성화",
            variable=self.joystick_visible,
            command=self.toggle_joystick_view,
            bg=card_bg,
            fg=text_main,
            selectcolor="#0f1b29",
            font=("맑은 고딕", 9, "bold"),
            activebackground=card_bg,
            activeforeground=text_main
        )
        self.js_check.pack(fill="x", padx=10, pady=(5, 4))

        self.center_group_frame = tk.LabelFrame(
            self.etc_frame,
            text="지도 표시 방식",
            fg=text_main,
            bg=card_bg,
            font=("맑은 고딕", 9, "bold")
        )
        self.center_group_frame.pack(fill="x", padx=10, pady=(4, 8))

        radio_opts = [
            ("지도 이동 없음", 0),
            ("홈 마커 중앙", 1),
            ("드론 중앙 추적", 2)
        ]
        for label, value in radio_opts:
            rb = tk.Radiobutton(
                self.center_group_frame,
                text=label,
                variable=self.map_center_mode,
                value=value,
                command=self.on_center_mode_change,
                bg=card_bg,
                fg=text_main,
                selectcolor="#0f1b29",
                font=("맑은 고딕", 9),
                activebackground=card_bg,
                activeforeground=text_main
            )
            rb.pack(anchor="w", padx=8, pady=1)

        self.map_style_frame = tk.LabelFrame(
            self.etc_frame,
            text="지도 스타일 변경",
            fg=text_main,
            bg=card_bg,
            font=("맑은 고딕", 9, "bold")
        )
        self.map_style_frame.pack(fill="x", padx=10, pady=(6, 8))

        self.map_style_var = tk.StringVar(value=getattr(self, "selected_map_label", "1. openstreetmap"))
        self.map_style_combo = ttk.Combobox(
            self.map_style_frame,
            textvariable=self.map_style_var,
            values=list(self.get_map_options().keys()),
            state="readonly",
            width=28
        )
        self.map_style_combo.pack(fill="x", padx=8, pady=(6, 4))

        make_button(
            self.map_style_frame,
            "현재 지도에 적용",
            self.apply_runtime_map_style,
            bg="#00d4ff",
            fg="#08111f",
            active="#00a9cc"
        ).pack(fill="x", padx=8, pady=(0, 8))

        # 명령 해설 패널은 지도 왼쪽 하단 오버레이로 이동함

        # 실습 리포트 패널
        report_frame = make_section(right_parent, "05  PRACTICE REPORT", "#ff4d6d")
        self.report_status_label = tk.Label(
            report_frame,
            text=f"세션: {self.session_id}\n기록 수: 0개\nCSV 저장 대기 중",
            bg="#0d1520",
            fg="#d7e8f7",
            justify="left",
            anchor="w",
            font=("맑은 고딕", 9),
            padx=8,
            pady=8
        )
        self.report_status_label.pack(fill="x", padx=10, pady=(0, 6))

        make_button(
            report_frame,
            "CSV 리포트 즉시 저장",
            self.save_practice_report,
            bg="#ff4d6d",
            fg="white",
            active="#d93657"
        ).pack(fill="x", padx=10, pady=(0, 8))

        self.add_practice_log("APP_READY", "SYSTEM", "READY", "프로그램 UI 초기화 완료")
    # -----------------------------------------------------------
    # 왼쪽 하단 명령 해설 패널
    # -----------------------------------------------------------
    def create_waypoint_panel(self):
        """
        지도 하단에 Mission Planner 스타일의 Waypoint 기록 패널 생성.

        시작 화면에서는 COMMAND EXPLAIN과 겹치지 않도록 숨김 상태로 둔다.
        실제 지도 클릭 / Guided 이동 / AI Route 실행 등 Waypoint 이벤트가 발생하면 자동으로 표시된다.
        """
        self.waypoint_panel = tk.Frame(
            self.map_widget,
            bg="#101923",
            highlightbackground="#00ffcc",
            highlightthickness=2,
            bd=0
        )
        self.waypoint_panel.pack_propagate(False)

        header = tk.Frame(self.waypoint_panel, bg="#00ffcc")
        header.pack(fill="x")

        self.waypoint_title_label = tk.Label(
            header,
            text="WAYPOINT LOG  |  지도 클릭/AI Route/Guided 이동 기록",
            bg="#00ffcc",
            fg="#07111f",
            font=("맑은 고딕", 9, "bold"),
            anchor="w"
        )
        self.waypoint_title_label.pack(side="left", fill="x", expand=True, padx=8, pady=4)

        self.waypoint_toggle_btn = tk.Button(
            header,
            text="축소",
            command=self.toggle_waypoint_expand,
            bg="#07111f",
            fg="white",
            relief="flat",
            width=6
        )
        self.waypoint_toggle_btn.pack(side="right", padx=4, pady=2)

        columns = ("seq", "command", "lat", "lon", "alt", "status", "source")
        self.waypoint_tree = ttk.Treeview(
            self.waypoint_panel,
            columns=columns,
            show="headings",
            height=5
        )

        col_defs = {
            "seq": ("No", 45),
            "command": ("Command", 105),
            "lat": ("Lat", 105),
            "lon": ("Lon", 105),
            "alt": ("Alt", 70),
            "status": ("Status", 95),
            "source": ("Source", 120),
        }

        for col, (title, width) in col_defs.items():
            self.waypoint_tree.heading(col, text=title)
            self.waypoint_tree.column(col, width=width, anchor="center")

        self.waypoint_tree.pack(fill="both", expand=True, padx=6, pady=(4, 6))

        # 시작 화면에서는 Waypoint Log를 숨긴다.
        # INIT 행도 표시하지 않아 화면이 더 깔끔하게 시작된다.
        self.hide_waypoint_panel()

    def show_waypoint_panel(self, expanded=True):
        """
        Waypoint 패널 표시.

        expanded=True:
        - 하단 중앙에 크게 표시
        - COMMAND EXPLAIN과 겹치지 않도록 COMMAND EXPLAIN을 자동 축소

        expanded=False:
        - 오른쪽 하단에 작은 헤더만 표시
        - COMMAND EXPLAIN과 거의 겹치지 않게 배치
        """
        if not hasattr(self, "waypoint_panel"):
            return

        self.waypoint_panel_visible = True
        self.waypoint_panel_expanded = bool(expanded)

        if self.waypoint_panel_expanded:
            try:
                if getattr(self, "command_explain_expanded", False):
                    self.collapse_command_explain_panel()
            except Exception:
                pass

            self.waypoint_panel.place(
                relx=0.5,
                rely=1.0,
                x=0,
                y=-15,
                anchor="s",
                width=680,
                height=170
            )
            if hasattr(self, "waypoint_tree"):
                self.waypoint_tree.pack(fill="both", expand=True, padx=6, pady=(4, 6))
            if hasattr(self, "waypoint_title_label"):
                self.waypoint_title_label.config(
                    text="WAYPOINT LOG  |  지도 클릭/AI Route/Guided 이동 기록"
                )
            if hasattr(self, "waypoint_toggle_btn"):
                self.waypoint_toggle_btn.config(text="축소")

        else:
            self.waypoint_panel.place(
                relx=1.0,
                rely=1.0,
                x=-15,
                y=-15,
                anchor="se",
                width=360,
                height=34
            )
            if hasattr(self, "waypoint_tree"):
                self.waypoint_tree.pack_forget()
            if hasattr(self, "waypoint_title_label"):
                self.waypoint_title_label.config(
                    text=f"WAYPOINT LOG 축소됨  |  {len(self.waypoint_records)}개 기록"
                )
            if hasattr(self, "waypoint_toggle_btn"):
                self.waypoint_toggle_btn.config(text="확대")

        try:
            self.waypoint_panel.lift()
        except Exception:
            pass

    def hide_waypoint_panel(self):
        """Waypoint 패널을 완전히 숨긴다."""
        if not hasattr(self, "waypoint_panel"):
            return

        self.waypoint_panel_visible = False
        self.waypoint_panel_expanded = False

        try:
            self.waypoint_panel.place_forget()
        except Exception:
            pass

    def toggle_waypoint_panel(self):
        """
        오른쪽 TOOLS 버튼용 보기/숨김 토글.

        - 숨김 상태: 크게 표시
        - 표시 상태: 완전히 숨김
        """
        if not hasattr(self, "waypoint_panel"):
            return

        if getattr(self, "waypoint_panel_visible", False):
            self.hide_waypoint_panel()
        else:
            self.show_waypoint_panel(expanded=True)

    def toggle_waypoint_expand(self):
        """
        Waypoint 패널 헤더 버튼용 확대/축소 토글.

        - 크게 열린 상태: 오른쪽 하단 작은 헤더로 축소
        - 축소 상태: 하단 중앙으로 확대
        """
        if not hasattr(self, "waypoint_panel"):
            return

        if not getattr(self, "waypoint_panel_visible", False):
            self.show_waypoint_panel(expanded=True)
            return

        self.show_waypoint_panel(expanded=not getattr(self, "waypoint_panel_expanded", False))

    def add_waypoint_record(self, command, lat, lon, alt="", status="SELECTED", source="MAP"):
        """Waypoint 테이블과 CSV 저장용 메모리에 기록 추가"""
        try:
            self.waypoint_seq += 1
            row = {
                "seq": self.waypoint_seq,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "command": command,
                "lat": round(float(lat), 7) if lat != "" else "",
                "lon": round(float(lon), 7) if lon != "" else "",
                "alt": alt,
                "status": status,
                "source": source,
                "mode": self.d_mode,
                "battery_pct": self.d_bat_p,
                "current_alt": round(self.d_alt, 2),
            }
            self.waypoint_records.append(row)

            if hasattr(self, "waypoint_tree"):
                self.waypoint_tree.insert(
                    "",
                    "end",
                    values=(
                        row["seq"],
                        row["command"],
                        row["lat"],
                        row["lon"],
                        row["alt"],
                        row["status"],
                        row["source"]
                    )
                )
                children = self.waypoint_tree.get_children()
                if children:
                    self.waypoint_tree.see(children[-1])

            if command not in ["INIT", "NO_WAYPOINT"]:
                self.show_waypoint_panel(expanded=True)

            self.add_practice_log(
                "WAYPOINT_RECORDED",
                command,
                status,
                f"seq={row['seq']}, lat={row['lat']}, lon={row['lon']}, alt={alt}, source={source}",
                "PASS"
            )

        except Exception as e:
            print(f"[WAYPOINT RECORD ERROR] {e}")

    def update_last_waypoint_status(self, command=None, alt=None, status="EXECUTED", source="GUIDED"):
        """마지막 waypoint 기록의 실행 상태/고도를 갱신한다."""
        try:
            if not self.waypoint_records:
                return

            row = self.waypoint_records[-1]
            if command:
                row["command"] = command
            if alt is not None:
                row["alt"] = alt
            row["status"] = status
            row["source"] = source

            if hasattr(self, "waypoint_tree"):
                children = self.waypoint_tree.get_children()
                if children:
                    item = children[-1]
                    self.waypoint_tree.item(
                        item,
                        values=(
                            row["seq"],
                            row["command"],
                            row["lat"],
                            row["lon"],
                            row["alt"],
                            row["status"],
                            row["source"]
                        )
                    )

            self.show_waypoint_panel(expanded=True)

        except Exception as e:
            print(f"[WAYPOINT UPDATE ERROR] {e}")

    def save_waypoint_report(self):
        """Waypoint 기록만 별도 CSV로 저장"""
        try:
            report_dir = os.path.join(current_dir, "practice_reports")
            os.makedirs(report_dir, exist_ok=True)

            if not self.waypoint_records:
                self.add_waypoint_record("NO_WAYPOINT", "", "", "", "EMPTY", "SYSTEM")

            filename = f"waypoint_report_{self.session_id}.csv"
            path = os.path.join(report_dir, filename)

            fieldnames = ["seq", "time", "command", "lat", "lon", "alt", "status", "source", "mode", "battery_pct", "current_alt"]

            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.waypoint_records)

            self.last_waypoint_report_path = path
            self.set_status(f"Waypoint 리포트 저장 완료: {filename}")
            print(f"[WAYPOINT REPORT] 저장 완료: {path}")
            return path

        except Exception as e:
            self.set_status(f"Waypoint 리포트 저장 실패: {e}")
            print(f"[WAYPOINT REPORT ERROR] {e}")
            return None

    def open_beginner_tutorial(self):
        """초보자 튜토리얼 오버레이 열기"""
        try:
            if hasattr(self, "tutorial_dialog") and self.tutorial_dialog and self.tutorial_dialog.winfo_exists():
                self.tutorial_dialog.lift()
                return
        except Exception:
            pass

        self.tutorial_dialog = BeginnerTutorialDialog(self)
        self.add_practice_log("TUTORIAL_OPEN", "UI_TUTORIAL", "OPENED", "초보자 튜토리얼 시작")

    def create_command_explain_panel(self):
        """지도 왼쪽 하단에 확대/축소 가능한 명령 해설 패널 생성"""
        self.command_explain_expanded = True

        self.command_explain_panel = tk.Frame(
            self.map_widget,
            bg="#07111f",
            highlightbackground="#8e7dff",
            highlightthickness=2,
            bd=0
        )
        self.command_explain_panel.place(
            relx=0.0,
            rely=1.0,
            x=15,
            y=-15,
            anchor="sw",
            width=620,
            height=230
        )
        self.command_explain_panel.pack_propagate(False)

        self.command_explain_header = tk.Frame(
            self.command_explain_panel,
            bg="#141f33",
            height=38
        )
        self.command_explain_header.pack(fill="x", side="top")
        self.command_explain_header.pack_propagate(False)

        tk.Label(
            self.command_explain_header,
            text="04  COMMAND EXPLAIN",
            bg="#141f33",
            fg="#f5f1ff",
            font=("맑은 고딕", 11, "bold")
        ).pack(side="left", padx=12)

        self.command_explain_toggle_btn = tk.Button(
            self.command_explain_header,
            text="축소",
            bg="#8e7dff",
            fg="white",
            activebackground="#6f5cff",
            activeforeground="white",
            relief="flat",
            bd=0,
            font=("맑은 고딕", 9, "bold"),
            cursor="hand2",
            command=self.toggle_command_explain_panel
        )
        self.command_explain_toggle_btn.pack(side="right", padx=8, pady=6)

        self.command_explain_text = tk.Text(
            self.command_explain_panel,
            bg="#0d1520",
            fg="#d7e8f7",
            insertbackground="white",
            relief="flat",
            bd=0,
            wrap="word",
            font=("Consolas", 10),
            padx=10,
            pady=8
        )
        self.command_explain_text.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        self.command_explain_text.insert(
            "end",
            "[대기]\n명령 버튼을 누르면 MAVLink 명령 이름, 실행 조건, 실행 결과가 표시됩니다.\n\n"
            "이 패널은 왼쪽 하단에서 크게 볼 수 있으며, 오른쪽 위 '축소' 버튼으로 접을 수 있습니다."
        )
        self.command_explain_text.config(state="disabled")

    def collapse_command_explain_panel(self):
        """COMMAND EXPLAIN 패널을 축소한다."""
        if not hasattr(self, "command_explain_panel"):
            return

        self.command_explain_expanded = False
        self.command_explain_panel.place_configure(width=310, height=44)
        self.command_explain_toggle_btn.config(text="확대")
        try:
            self.command_explain_text.pack_forget()
        except Exception:
            pass

    def expand_command_explain_panel(self):
        """COMMAND EXPLAIN 패널을 확대한다. Waypoint가 크게 열려 있으면 자동 축소한다."""
        if not hasattr(self, "command_explain_panel"):
            return

        try:
            if getattr(self, "waypoint_panel_visible", False) and getattr(self, "waypoint_panel_expanded", False):
                self.show_waypoint_panel(expanded=False)
        except Exception:
            pass

        self.command_explain_expanded = True
        self.command_explain_panel.place_configure(width=620, height=230)
        self.command_explain_toggle_btn.config(text="축소")
        try:
            self.command_explain_text.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        except Exception:
            pass

    def toggle_command_explain_panel(self):
        """명령 해설 패널을 크게/작게 전환"""
        if not hasattr(self, "command_explain_panel"):
            return

        if getattr(self, "command_explain_expanded", False):
            self.collapse_command_explain_panel()
        else:
            self.expand_command_explain_panel()

    # -----------------------------------------------------------
    # UI 보조 함수
    # -----------------------------------------------------------
    def set_status(self, text):
        """상태바 갱신. 종료 중에는 after 콜백을 새로 만들지 않는다."""
        try:
            if getattr(self, "is_closing", False) or not self.winfo_exists():
                print(f"[STATUS] {text}")
                return

            self.after(0, lambda: self.status_var.set(text))
        except Exception:
            print(f"[STATUS] {text}")

    def update_command_explain(self, title, mavlink_command, conditions="", result=""):
        """오른쪽 명령 해설 패널 갱신"""
        if not hasattr(self, "command_explain_text"):
            return

        try:
            # 명령 해설이 새로 갱신될 때는 내용을 읽을 수 있도록 COMMAND EXPLAIN을 우선 표시한다.
            # 단, Waypoint Log가 크게 열려 있으면 오른쪽 하단 축소 상태로 내려 겹침을 방지한다.
            if getattr(self, "waypoint_panel_visible", False) and getattr(self, "waypoint_panel_expanded", False):
                self.show_waypoint_panel(expanded=False)
            if not getattr(self, "command_explain_expanded", False):
                self.expand_command_explain_panel()
        except Exception:
            pass

        message = (
            f"[{title}]\n"
            f"MAVLink: {mavlink_command}\n\n"
            f"실행 조건:\n{conditions}\n\n"
            f"결과:\n{result}"
        )

        def apply():
            try:
                self.command_explain_text.config(state="normal")
                self.command_explain_text.delete("1.0", tk.END)
                self.command_explain_text.insert(tk.END, message)
                self.command_explain_text.config(state="disabled")
            except Exception:
                pass

        self.after(0, apply)

    def explain_failure(self, title, mavlink_command, reason, solution, log_action="COMMAND_BLOCKED"):
        """
        초보자용 실패 원인 번역기.
        명령이 실행되지 않았을 때 원인과 다음 조치를 COMMAND EXPLAIN 패널에 표시한다.
        """
        message_result = (
            f"명령이 차단되었습니다.\n\n"
            f"[실패 원인]\n{reason}\n\n"
            f"[해결 방법]\n{solution}"
        )

        self.update_command_explain(
            title,
            mavlink_command,
            "- 현재 상태값을 기준으로 명령 실행 가능 여부를 검사함",
            message_result
        )

        self.set_status(f"{title}: {reason}")
        self.add_practice_log(log_action, mavlink_command, "BLOCKED", reason, f"FAIL: {reason}")

    def get_beginner_step_state(self):
        """
        단계별 비행 절차 안내용 상태 계산.
        True/False와 권장 다음 행동 문구를 반환한다.
        """
        sitl_proc = getattr(self.sitl, "proc", None)
        sitl_ok = bool(sitl_proc is not None and sitl_proc.poll() is None)
        mavlink_ok = self.drone is not None
        gps_ready = (
            self.gps_enable_time is not None
            and time.time() >= self.gps_enable_time
            and self.d_lat != 0
            and self.d_lon != 0
        )

        mode_name = str(self.d_mode).strip().upper()
        mode_ok = mode_name in ["GUIDED", "LOITER", "ALT_HOLD", "STABILIZE", "AUTO", "RTL", "LAND"]
        arm_ok = bool(self.d_is_armed or self.force_ui_armed)
        takeoff_ok = self.d_alt >= 0.5
        control_ok = bool(
            self.target_marker is not None
            or self.joystick_visible.get()
            or self.d_speed > 0.2
        )
        finish_ok = mode_name in ["LAND", "RTL"] or (sitl_ok and not arm_ok and takeoff_ok)

        states = {
            "sitl": sitl_ok,
            "mavlink": mavlink_ok,
            "gps": gps_ready,
            "mode": mode_ok and mavlink_ok,
            "arm": arm_ok,
            "takeoff": takeoff_ok,
            "control": control_ok,
            "finish": finish_ok
        }

        if not sitl_ok:
            recommend = "권장 다음 단계: Start SITL을 눌러 시뮬레이터를 실행하세요."
        elif not mavlink_ok:
            recommend = "권장 다음 단계: MAVLink 연결 완료를 기다리세요."
        elif not gps_ready:
            recommend = "권장 다음 단계: GPS 안정화 대기 후 위도/경도 값이 들어오는지 확인하세요."
        elif not mode_ok:
            recommend = "권장 다음 단계: GUIDED 또는 LOITER 모드를 선택하세요."
        elif not arm_ok:
            recommend = "권장 다음 단계: ARM / 시동 버튼을 누르세요."
        elif not takeoff_ok:
            recommend = "권장 다음 단계: TAKEOFF 버튼으로 이륙하세요."
        elif not control_ok:
            recommend = "권장 다음 단계: 지도 클릭 이동 또는 조이스틱 조종을 연습하세요."
        elif not finish_ok:
            recommend = "권장 다음 단계: LAND 또는 RTL 후 DISARM으로 실습을 종료하세요."
        else:
            recommend = "권장 다음 단계: Exit SITL을 눌러 CSV 실습 리포트를 저장하세요."

        return states, recommend

    def update_beginner_guide(self):
        """오른쪽 패널의 단계별 비행 절차 안내를 현재 상태에 맞게 갱신한다."""
        if not hasattr(self, "beginner_guide_labels"):
            return

        try:
            states, recommend = self.get_beginner_step_state()

            for key, label_text in getattr(self, "beginner_guide_order", []):
                label = self.beginner_guide_labels.get(key)
                if not label:
                    continue

                if states.get(key):
                    label.config(
                        text=f"■ {label_text}",
                        fg="#7CFF6B",
                        bg="#102A22"
                    )
                else:
                    label.config(
                        text=f"□ {label_text}",
                        fg="#8395a7",
                        bg="#0d1520"
                    )

            if hasattr(self, "beginner_recommend_label"):
                self.beginner_recommend_label.config(text=recommend)

        except Exception as e:
            print(f"[BEGINNER GUIDE ERROR] {e}")

    def check_basic_gate(self, command_name, need_drone=True, need_arm=False, need_gps=False, need_airborne=False, allowed_modes=None):
        """
        안전 게이트 + 실패 원인 번역기.
        명령 실행 전 필요한 조건을 검사하고, 실패 시 초보자용 원인/해결 방법을 표시한다.
        """
        allowed_modes = allowed_modes or []

        if need_drone and not self.drone:
            self.explain_failure(
                f"{command_name} 차단",
                "SAFETY_GATE",
                "드론이 MAVLink로 연결되지 않았습니다.",
                "1. Start SITL을 먼저 실행하세요.\n2. 상태바에 '드론 연결 성공'이 표시될 때까지 기다리세요."
            )
            return False

        if need_gps:
            gps_ready = (
                self.gps_enable_time is not None
                and time.time() >= self.gps_enable_time
                and self.d_lat != 0
                and self.d_lon != 0
            )
            if not gps_ready:
                self.explain_failure(
                    f"{command_name} 차단",
                    "SAFETY_GATE",
                    "GPS 위치 정보가 아직 안정화되지 않았습니다.",
                    "1. GPS 초기화 카운트다운이 끝날 때까지 기다리세요.\n2. HUD의 위성 수와 위도/경도 값이 들어오는지 확인하세요."
                )
                return False

        if allowed_modes:
            current_mode = str(self.d_mode).strip().upper()
            allowed_upper = [m.upper() for m in allowed_modes]
            if current_mode not in allowed_upper:
                self.explain_failure(
                    f"{command_name} 차단",
                    "SAFETY_GATE",
                    f"현재 모드가 {current_mode}입니다. 이 명령은 {', '.join(allowed_upper)} 모드에서 권장됩니다.",
                    f"1. 오른쪽 FLIGHT MODE에서 {allowed_upper[0]} 모드를 선택하세요.\n2. 모드 변경 후 다시 명령을 실행하세요."
                )
                return False

        if need_arm and not (self.d_is_armed or self.force_ui_armed):
            self.explain_failure(
                f"{command_name} 차단",
                "SAFETY_GATE",
                "드론이 ARM 상태가 아닙니다.",
                "1. 비행 모드를 GUIDED 또는 LOITER로 변경하세요.\n2. ARM / 시동 버튼을 누르세요.\n3. 상태가 ARMED로 바뀐 뒤 다시 실행하세요."
            )
            return False

        if need_airborne and self.d_alt < 0.5:
            self.explain_failure(
                f"{command_name} 차단",
                "SAFETY_GATE",
                f"현재 상대고도가 {self.d_alt:.1f}m입니다. 드론이 아직 이륙하지 않은 상태입니다.",
                "1. TAKEOFF 버튼을 눌러 먼저 이륙하세요.\n2. 상대고도가 0.5m 이상이 된 뒤 이동 명령을 실행하세요."
            )
            return False

        return True

    def add_practice_log(self, action, mavlink_command="", result="", detail="", safety_result=""):
        """실습 이벤트를 고정 CSV 포맷으로 저장할 수 있도록 메모리에 기록"""
        try:
            now = datetime.now()
            elapsed = round(time.time() - self.session_start_ts, 2)

            target_lat = ""
            target_lon = ""
            if self.target_marker:
                try:
                    target_lat, target_lon = self.target_marker.position
                except Exception:
                    pass

            row = {
                "session_id": self.session_id,
                "event_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed_sec": elapsed,
                "action": action,
                "mavlink_command": mavlink_command,
                "result": result,
                "detail": detail,
                "safety_result": safety_result,
                "mode": self.d_mode,
                "armed": self.d_is_armed,
                "alt_m": round(self.d_alt, 2),
                "speed_mps": round(self.d_speed, 2),
                "sats": self.d_sats,
                "battery_v": round(self.d_bat_v, 2),
                "battery_pct": self.d_bat_p,
                "lat": self.d_lat,
                "lon": self.d_lon,
                "target_lat": target_lat,
                "target_lon": target_lon,
            }

            self.practice_logs.append(row)
            self.report_saved = False
            self.update_report_panel()
        except Exception as e:
            print(f"[REPORT LOG ERROR] {e}")

    def update_report_panel(self):
        if not hasattr(self, "report_status_label"):
            return

        save_state = "CSV 저장 완료" if self.report_saved else "CSV 저장 대기 중"
        last_path = self.last_report_path if self.last_report_path else "-"
        text = (
            f"세션: {self.session_id}\n"
            f"기록 수: {len(self.practice_logs)}개\n"
            f"상태: {save_state}\n"
            f"파일: {os.path.basename(last_path) if last_path != '-' else '-'}"
        )

        try:
            self.report_status_label.config(text=text)
        except Exception:
            pass

    def save_practice_report(self):
        """현재 실습 기록을 CSV 파일로 저장"""
        try:
            report_dir = os.path.join(current_dir, "practice_reports")
            os.makedirs(report_dir, exist_ok=True)

            if not self.practice_logs:
                self.add_practice_log("NO_EVENT", "SYSTEM", "EMPTY", "저장 시점에 기록된 조작이 없어 빈 리포트 생성")

            filename = f"practice_report_{self.session_id}.csv"
            report_path = os.path.join(report_dir, filename)

            fieldnames = [
                "session_id",
                "event_time",
                "elapsed_sec",
                "action",
                "mavlink_command",
                "result",
                "detail",
                "safety_result",
                "mode",
                "armed",
                "alt_m",
                "speed_mps",
                "sats",
                "battery_v",
                "battery_pct",
                "lat",
                "lon",
                "target_lat",
                "target_lon",
            ]

            with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.practice_logs)

            self.last_report_path = report_path
            self.report_saved = True
            self.update_report_panel()

            try:
                self.save_waypoint_report()
            except Exception as e:
                print(f"[WAYPOINT AUTO SAVE ERROR] {e}")

            self.set_status(f"실습 리포트 저장 완료: {filename}")
            print(f"[REPORT] 실습 리포트 저장 완료: {report_path}")
            return report_path

        except Exception as e:
            self.set_status(f"실습 리포트 저장 실패: {e}")
            print(f"[REPORT ERROR] {e}")
            return None

    def reset_telemetry(self):
        self.d_roll = 0.0
        self.d_pitch = 0.0
        self.d_alt = 0.0
        self.d_speed = 0.0
        self.d_mode = "DISCONN"
        self.d_sats = 0
        self.d_bat_v = 0.0
        self.d_bat_p = 0
        self.d_is_armed = False
        self.d_lat = 0.0
        self.d_lon = 0.0
        self.d_yaw = 0.0
        self.last_drone_yaw = -1.0
        self.force_ui_armed = False
        self.is_ready_to_arm = False
        self.prearm_msg = "점검 중"
        self.last_mode = None
        self.disarm_time = None
        self.js_roll = 1500
        self.js_pitch = 1500
        self.js_throttle = 1500
        self.js_yaw = 1500

    def clear_drone_marker(self):
        if self.drone_marker:
            try:
                self.drone_marker.delete()
            except Exception as e:
                print("[DRONE MARKER DELETE ERROR]", e)
            self.drone_marker = None

    def clear_home_marker(self):
        if self.home_marker:
            try:
                self.home_marker.delete()
            except Exception as e:
                print("[HOME MARKER DELETE ERROR]", e)
            self.home_marker = None

    def clear_target_marker_and_path(self):
        if self.target_marker:
            try:
                self.target_marker.delete()
            except Exception as e:
                print("[TARGET MARKER DELETE ERROR]", e)
            self.target_marker = None

        if self.path_line:
            try:
                self.path_line.delete()
            except Exception as e:
                print("[PATH LINE DELETE ERROR]", e)
            self.path_line = None

        self.clear_active_route_visual()

    def set_active_route_visual(self, lat, lon, alt, label="실행 중 경로"):
        """
        실제 이동 명령이 전송된 목적지와 경로선을 별도로 표시한다.

        기존 target_marker/path_line은 사용자가 지도 클릭을 다시 하면 새 목표로 바뀐다.
        그래서 실제 이동 중인 경로를 보존하기 위해 active_route_marker/active_route_line을 따로 둔다.
        """
        try:
            self.active_route_target = (float(lat), float(lon), float(alt))
            self.active_route_label = label

            if self.active_route_marker:
                try:
                    self.active_route_marker.delete()
                except Exception:
                    pass
                self.active_route_marker = None

            if self.target_icon:
                self.active_route_marker = self.map_widget.set_marker(
                    lat,
                    lon,
                    text="ACTIVE ROUTE",
                    icon=self.target_icon
                )
            else:
                self.active_route_marker = self.map_widget.set_marker(
                    lat,
                    lon,
                    text="ACTIVE ROUTE"
                )

            if self.active_route_line:
                try:
                    self.active_route_line.delete()
                except Exception:
                    pass
                self.active_route_line = None

            if self.d_lat and self.d_lon:
                self.active_route_line = self.map_widget.set_path(
                    [(self.d_lat, self.d_lon), (lat, lon)],
                    color="#00ffcc",
                    width=4
                )

            self.add_practice_log(
                "ACTIVE_ROUTE_SET",
                "MAP_ROUTE_VISUAL",
                "SET",
                f"label={label}, lat={lat}, lon={lon}, alt={alt}",
                "PASS"
            )

        except Exception as e:
            print(f"[ACTIVE ROUTE VISUAL ERROR] {e}")

    def clear_active_route_visual(self):
        """실제 실행 경로 표시를 제거한다."""
        if self.active_route_marker:
            try:
                self.active_route_marker.delete()
            except Exception as e:
                print("[ACTIVE ROUTE MARKER DELETE ERROR]", e)
            self.active_route_marker = None

        if self.active_route_line:
            try:
                self.active_route_line.delete()
            except Exception as e:
                print("[ACTIVE ROUTE LINE DELETE ERROR]", e)
            self.active_route_line = None

        self.active_route_target = None
        self.active_route_label = ""

    def close_countdown_popup(self):
        if self.countdown_popup:
            try:
                self.countdown_popup.destroy()
            except:
                pass
            self.countdown_popup = None

    def return_to_home_view(self):
        try:
            self.map_widget.set_position(self.last_home_lat, self.last_home_lon)
            self.map_widget.set_zoom(17)
        except Exception as e:
            print("[RETURN HOME ERROR]", e)

    # -----------------------------------------------------------
    # 프리셋 / AI Route Assist 기능
    # -----------------------------------------------------------
    def activate_beginner_preset(self):
        """Preset 1: 초보자 학습 모드"""
        self.control_preset.set(1)
        self.update_command_explain(
            "Preset 1 - 초보자 학습 모드",
            "UI_PRESET",
            "- 단계별 비행 절차 안내\n- 실패 원인 번역기\n- MAVLink 명령 해설\n- 실습 리포트 자동 저장",
            "초보자 학습 모드가 활성화되었습니다. 오른쪽 BEGINNER FLIGHT GUIDE 순서대로 실습하세요."
        )
        self.add_practice_log("PRESET_CHANGE", "UI_PRESET", "CHANGED", "Preset 1 Beginner Training")
        self.set_status("Preset 1: 초보자 학습 모드")

    def activate_ai_route_preset(self):
        """Preset 2: AI Route Assist 모드"""
        self.control_preset.set(2)
        self.update_command_explain(
            "Preset 2 - AI Route Assist 모드",
            "OLLAMA_API + ROUTE_RULE_ENGINE",
            "- 사용자가 목표 좌표와 자연어 요청 입력\n- Ollama가 의도/우선순위 분석\n- GCS 규칙 엔진이 건물 높이와 고도 효율을 계산\n- Preset 3에서 배터리 도달 가능성까지 확장 검증 가능\n- 사용자 승인 후 Guided 이동 실행",
            "AI 주행 보조 모드가 활성화되었습니다. 오른쪽 TOOLS의 'AI Route Assist 열기'를 누르세요.\n\n주의: LLM은 직접 조종하지 않고 계획 보조만 수행합니다. 실제 명령은 안전 게이트와 사용자 승인을 통과한 뒤 전송됩니다."
        )
        self.add_practice_log("PRESET_CHANGE", "UI_PRESET", "CHANGED", "Preset 2 AI Route Assist")
        self.set_status("Preset 2: AI Route Assist 모드")
        self.open_ai_route_assist_dialog()

    def activate_battery_safe_preset(self):
        """Preset 3: 배터리 안전 경로 검증 모드"""
        self.control_preset.set(3)
        self.update_command_explain(
            "Preset 3 - Battery Safe Route 모드",
            "BATTERY_FEASIBILITY_GATE",
            "- 현재 배터리 잔량 확인\n- 목표 거리 + HOME 복귀 거리 계산\n- 추천 고도까지 상승 비용 계산\n- 예비 배터리 기준 검증\n- SAFE / CAUTION / NOT_RECOMMENDED 판정",
            "배터리 안전성 확장 모드가 활성화되었습니다.\n\n"
            "Preset 2에서 만든 AI Route 계획을 현재 배터리 기준으로 검증하고, "
            "목표 지점 이동 후 HOME 복귀까지 가능한지 교육용 추정값으로 판단합니다."
        )
        self.add_practice_log("PRESET_CHANGE", "UI_PRESET", "CHANGED", "Preset 3 Battery Safe Route")
        self.set_status("Preset 3: Battery Safe Route 모드")
        self.open_battery_safe_dialog()

    def open_battery_safe_dialog(self):
        """Battery Safe Check 팝업 열기"""
        try:
            if self.battery_safe_dialog and self.battery_safe_dialog.winfo_exists():
                self.battery_safe_dialog.lift()
                return
        except Exception:
            pass

        self.control_preset.set(3)
        self.battery_safe_dialog = BatterySafeDialog(self)

    def open_ai_route_assist_dialog(self):
        """AI Route Assist 팝업 열기"""
        try:
            if self.ai_route_dialog and self.ai_route_dialog.winfo_exists():
                self.ai_route_dialog.lift()
                return
        except Exception:
            pass

        self.control_preset.set(2)
        self.ai_route_dialog = AIRouteAssistDialog(self)

    def get_current_ai_target_or_default(self):
        """AI Route Assist 기본 목표 좌표를 가져온다. TARGET이 있으면 TARGET, 없으면 현재 위치 근처를 사용."""
        try:
            if self.target_marker:
                lat, lon = self.target_marker.position
                return float(lat), float(lon)
        except Exception:
            pass

        if self.d_lat != 0 and self.d_lon != 0:
            return self.d_lat + 0.00045, self.d_lon + 0.00045

        return self.last_home_lat + 0.00045, self.last_home_lon + 0.00045

    def set_ai_target_from_address(self, lat, lon, label="주소 변환 목표"):
        """
        주소 변환 또는 AI Route 팝업에서 입력한 좌표를 TARGET 마커로 반영한다.

        주의:
        - 이 함수는 이동 명령을 보내지 않는다.
        - 지도 목표 마커만 갱신하고 AI Route 계산의 목표 좌표로 사용한다.
        """
        try:
            if self.target_marker:
                try:
                    self.target_marker.delete()
                except Exception:
                    pass
                self.target_marker = None

            marker_text = "AI TARGET"

            if self.target_icon:
                self.target_marker = self.map_widget.set_marker(
                    lat,
                    lon,
                    text=marker_text,
                    icon=self.target_icon
                )
            else:
                self.target_marker = self.map_widget.set_marker(
                    lat,
                    lon,
                    text=marker_text
                )

            self.map_widget.set_position(lat, lon)

            distance_m = self.calc_distance_m(self.d_lat, self.d_lon, lat, lon) if self.d_lat and self.d_lon else 0.0
            bearing_deg = self.calc_bearing_deg(self.d_lat, self.d_lon, lat, lon) if self.d_lat and self.d_lon else 0.0

            detail = (
                f"주소/장소명 변환으로 AI Route 목표를 설정했습니다.\n"
                f"검색 결과: {label}\n"
                f"lat={lat:.6f}, lon={lon:.6f}\n"
                f"현재 위치 기준 거리={distance_m:.1f}m, 방위각={bearing_deg:.1f}°\n\n"
                f"이 단계에서는 이동 명령을 보내지 않습니다. "
                f"Ollama 분석 또는 규칙 기반 분석 후 추천 고도 실행을 눌러야 실제 Guided 이동이 실행됩니다."
            )

            self.update_command_explain(
                "주소 기반 AI 목표 설정",
                "GEOCODING → TARGET",
                "- 주소 또는 장소명을 위도/경도로 변환\n"
                "- 변환된 좌표를 AI TARGET 마커로 표시\n"
                "- 이후 AI Route Assist에서 안전고도와 배터리 효율 계산",
                detail
            )
            self.set_status("주소 변환 좌표를 AI TARGET으로 설정했습니다.")
            self.add_practice_log(
                "AI_TARGET_GEOCODED",
                "GEOCODING",
                "SET",
                f"label={label}, lat={lat:.6f}, lon={lon:.6f}, distance_m={distance_m:.1f}"
            )

        except Exception as e:
            self.set_status(f"AI TARGET 설정 오류: {e}")
            print("[AI TARGET SET ERROR]", e)

    def call_ollama_route_intent(self, user_prompt, model="llama3.2"):
        """
        Ollama에 자연어 요청을 보내고 JSON 의도 분석 결과를 받는다.
        실패하면 예외를 발생시키고, 호출 측에서 규칙 기반 분석으로 대체할 수 있다.
        """
        system_prompt = f"""
너는 드론 GCS의 비행 계획 보조 모듈이다.
절대 직접 조종 명령을 만들지 말고, 사용자 요청의 의도만 JSON으로 분류한다.
응답은 JSON 하나만 출력한다.

허용 JSON 스키마:
{{
  "intent": "safe_energy_route" | "safe_route" | "energy_route" | "normal_guided_route",
  "priority": ["avoid_building", "save_battery", "short_distance"],
  "safety_margin_m": number,
  "max_altitude_m": number,
  "explanation": "짧은 한국어 설명"
}}

판단 규칙:
- 건물, 장애물, 안전, 회피가 중요하면 avoid_building 우선
- 배터리, 효율, 오래, 아끼다가 중요하면 save_battery 우선
- 빠르게, 짧게가 중요하면 short_distance 포함
- safety_margin_m은 보통 10~25 사이
- max_altitude_m은 보통 50~120 사이

사용자 요청: {user_prompt}
""".strip()

        payload = {
            "model": model,
            "prompt": system_prompt,
            "stream": False,
            "format": "json"
        }

        response = requests.post(
            "http://localhost:11434/api/generate",
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("response", "").strip()

        try:
            parsed = json.loads(raw)
        except Exception:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise ValueError(f"Ollama JSON 파싱 실패: {raw[:200]}")
            parsed = json.loads(match.group(0))

        return parsed

    def infer_route_intent_by_rule(self, user_prompt, safety_margin_m=15, max_altitude_m=80):
        """Ollama가 없을 때 사용하는 키워드 기반 의도 분석."""
        prompt = (user_prompt or "").lower()
        priority = []

        if any(k in prompt for k in ["건물", "장애물", "회피", "피해서", "안전", "높이"]):
            priority.append("avoid_building")
        if any(k in prompt for k in ["배터리", "효율", "아끼", "절약", "오래"]):
            priority.append("save_battery")
        if any(k in prompt for k in ["빠르게", "최단", "짧게", "직선"]):
            priority.append("short_distance")

        if not priority:
            priority = ["avoid_building", "save_battery"]

        if "avoid_building" in priority and safety_margin_m < 15:
            safety_margin_m = 15
        if "save_battery" in priority and safety_margin_m > 20:
            safety_margin_m = 20

        if "avoid_building" in priority and "save_battery" in priority:
            intent = "safe_energy_route"
        elif "avoid_building" in priority:
            intent = "safe_route"
        elif "save_battery" in priority:
            intent = "energy_route"
        else:
            intent = "normal_guided_route"

        return {
            "intent": intent,
            "priority": priority,
            "safety_margin_m": safety_margin_m,
            "max_altitude_m": max_altitude_m,
            "explanation": "Ollama 없이 키워드 기반 규칙으로 의도를 분석했습니다."
        }

    def distance_point_to_segment_m(self, p_lat, p_lon, a_lat, a_lon, b_lat, b_lon):
        """건물이 현재 위치-목표 위치 경로 주변에 있는지 보기 위한 단순 평면 근사 거리."""
        try:
            mean_lat = math.radians((a_lat + b_lat) / 2)
            m_per_deg_lat = 111_320.0
            m_per_deg_lon = 111_320.0 * math.cos(mean_lat)

            ax, ay = a_lon * m_per_deg_lon, a_lat * m_per_deg_lat
            bx, by = b_lon * m_per_deg_lon, b_lat * m_per_deg_lat
            px, py = p_lon * m_per_deg_lon, p_lat * m_per_deg_lat

            dx = bx - ax
            dy = by - ay
            if dx == 0 and dy == 0:
                return math.sqrt((px - ax) ** 2 + (py - ay) ** 2)

            t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
            t = max(0.0, min(1.0, t))
            cx = ax + t * dx
            cy = ay + t * dy
            return math.sqrt((px - cx) ** 2 + (py - cy) ** 2)
        except Exception:
            return 999999.0

    def find_relevant_buildings_for_route(self, start_lat, start_lon, target_lat, target_lon, corridor_width_m=70):
        """현재 위치-목표 위치 주변 샘플 건물 중 경로에 영향을 줄 수 있는 건물 추출."""
        relevant = []
        for b in self.sample_buildings:
            dist_to_path = self.distance_point_to_segment_m(
                b["lat"], b["lon"],
                start_lat, start_lon,
                target_lat, target_lon
            )
            dist_to_target = self.calc_distance_m(b["lat"], b["lon"], target_lat, target_lon)
            if dist_to_path <= corridor_width_m or dist_to_target <= 100:
                item = dict(b)
                item["dist_to_path_m"] = round(dist_to_path, 1)
                item["dist_to_target_m"] = round(dist_to_target, 1)
                relevant.append(item)
        return relevant

    def build_ai_route_plan(self, user_prompt, target_lat, target_lon, model="llama3.2", safety_margin_m=15, max_altitude_m=80, use_ollama=True):
        """LLM 의도 분석 + 규칙 기반 안전고도/배터리효율 계산."""
        start_lat = self.d_lat if self.d_lat != 0 else self.last_home_lat
        start_lon = self.d_lon if self.d_lon != 0 else self.last_home_lon
        current_alt = max(0.0, float(self.d_alt))
        distance_m = self.calc_distance_m(start_lat, start_lon, target_lat, target_lon)
        bearing_deg = self.calc_bearing_deg(start_lat, start_lon, target_lat, target_lon)

        ollama_used = False
        ollama_error = ""
        if use_ollama:
            try:
                intent = self.call_ollama_route_intent(user_prompt, model=model)
                ollama_used = True
            except Exception as e:
                ollama_error = str(e)
                intent = self.infer_route_intent_by_rule(user_prompt, safety_margin_m, max_altitude_m)
        else:
            intent = self.infer_route_intent_by_rule(user_prompt, safety_margin_m, max_altitude_m)

        priority = intent.get("priority", []) or []
        try:
            safety_margin_m = float(intent.get("safety_margin_m", safety_margin_m))
        except Exception:
            safety_margin_m = float(safety_margin_m)
        try:
            max_altitude_m = float(intent.get("max_altitude_m", max_altitude_m))
        except Exception:
            max_altitude_m = float(max_altitude_m)

        if "avoid_building" in priority and safety_margin_m < 15:
            safety_margin_m = 15
        if "save_battery" in priority and safety_margin_m > 22:
            safety_margin_m = 22

        relevant_buildings = self.find_relevant_buildings_for_route(start_lat, start_lon, target_lat, target_lon)
        max_building_h = max([b["height_m"] for b in relevant_buildings], default=0)
        highest_building = max(relevant_buildings, key=lambda x: x["height_m"], default=None)

        min_safe_alt = max_building_h + safety_margin_m
        min_safe_alt = max(10.0, min_safe_alt)
        if min_safe_alt > max_altitude_m:
            # 최대고도를 너무 낮게 잡은 경우, 안전 조건을 우선으로 올린다.
            max_altitude_m = min_safe_alt + 10

        raw_candidates = [
            min_safe_alt,
            min_safe_alt + 10,
            min_safe_alt + 20,
            max(20.0, current_alt + 5),
            max(20.0, min(max_altitude_m, 35.0)),
        ]
        candidates = sorted(set(round(min(max(c, 5.0), max_altitude_m), 1) for c in raw_candidates))

        candidate_rows = []
        for alt in candidates:
            climb = max(0.0, alt - current_alt)
            descent = max(0.0, current_alt - alt)
            safety_penalty = 9999.0 if alt < min_safe_alt else 0.0
            # 교육용 단순 에너지 추정식. 실제 배터리 모델이 아니라 비교 지표다.
            energy_score = (
                distance_m * 1.0
                + climb * 3.0
                + descent * 1.2
                + alt * 0.15
                + safety_penalty
            )
            if "save_battery" in priority:
                energy_score += climb * 0.8
            if "avoid_building" in priority and alt < (min_safe_alt + 5):
                energy_score += 25
            candidate_rows.append({
                "alt_m": alt,
                "climb_m": round(climb, 1),
                "energy_score": round(energy_score, 1),
                "safe": alt >= min_safe_alt
            })

        valid_candidates = [c for c in candidate_rows if c["safe"]]
        best = min(valid_candidates or candidate_rows, key=lambda x: x["energy_score"])

        plan = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_prompt": user_prompt,
            "model": model,
            "ollama_used": ollama_used,
            "ollama_error": ollama_error,
            "intent": intent.get("intent", "normal_guided_route"),
            "priority": priority,
            "llm_explanation": intent.get("explanation", ""),
            "start_lat": start_lat,
            "start_lon": start_lon,
            "target_lat": target_lat,
            "target_lon": target_lon,
            "distance_m": round(distance_m, 1),
            "bearing_deg": round(bearing_deg, 1),
            "safety_margin_m": round(safety_margin_m, 1),
            "max_altitude_m": round(max_altitude_m, 1),
            "relevant_buildings": relevant_buildings,
            "highest_building": highest_building,
            "min_safe_alt_m": round(min_safe_alt, 1),
            "candidates": candidate_rows,
            "recommended_alt_m": float(best["alt_m"]),
            "recommended_energy_score": best["energy_score"],
        }
        return plan

    def ensure_ai_route_plan_for_battery_safe(self):
        """
        Battery Safe 검증에 사용할 AI Route 계획을 보장한다.
        기존 계획이 있으면 사용하고, 없으면 현재 TARGET/기본 좌표로 규칙 기반 계획을 생성한다.
        """
        if self.ai_route_plan:
            return self.ai_route_plan

        lat, lon = self.get_current_ai_target_or_default()
        plan = self.build_ai_route_plan(
            user_prompt="배터리 안전성을 고려해서 목표 지점까지 이동 가능한지 검증해줘",
            target_lat=lat,
            target_lon=lon,
            model="rule-engine",
            safety_margin_m=15,
            max_altitude_m=80,
            use_ollama=False
        )
        self.ai_route_plan = plan
        return plan

    def build_battery_feasibility_check(self, plan, battery_pct=None, reserve_pct=20, alt_buffer_m=5, hover_pct=4, min_takeoff_pct=None):
        """
        목표 이동 + HOME 복귀까지의 배터리 실행 가능성 추정.

        실제 기체의 정밀 물리 모델이 아니라 교육용 추정식이다.
        드론 프로파일에 설정된 최소 이륙 배터리, 수평/상승/하강 소모 계수를 사용한다.
        """
        if not plan:
            raise ValueError("배터리 검증에 사용할 AI Route 계획이 없습니다.")

        start_lat = self.d_lat if self.d_lat != 0 else self.last_home_lat
        start_lon = self.d_lon if self.d_lon != 0 else self.last_home_lon
        current_alt = max(0.0, float(self.d_alt))

        target_lat = float(plan["target_lat"])
        target_lon = float(plan["target_lon"])
        target_alt = float(plan.get("recommended_alt_m", max(20.0, current_alt + 5.0))) + float(alt_buffer_m)

        if battery_pct is None:
            battery_pct = self.d_bat_p if self.d_bat_p and self.d_bat_p > 0 else 50.0

        profile = getattr(self, "drone_profile", {})
        if min_takeoff_pct is None:
            min_takeoff_pct = float(profile.get("min_takeoff_battery_pct", 70.0))

        battery_pct = float(battery_pct)
        reserve_pct = float(reserve_pct)
        hover_pct = float(hover_pct)
        min_takeoff_pct = float(min_takeoff_pct)

        outbound_m = self.calc_distance_m(start_lat, start_lon, target_lat, target_lon)
        return_m = self.calc_distance_m(target_lat, target_lon, self.last_home_lat, self.last_home_lon)
        total_horizontal_m = outbound_m + return_m

        climb_m = max(0.0, target_alt - current_alt)
        descent_m = max(0.0, current_alt - target_alt)

        horizontal_rate = float(profile.get("horizontal_pct_per_100m", 1.20))
        climb_rate = float(profile.get("climb_pct_per_10m", 1.60))
        descent_rate = float(profile.get("descent_pct_per_10m", 0.45))

        horizontal_pct = (total_horizontal_m / 100.0) * horizontal_rate
        climb_pct = (climb_m / 10.0) * climb_rate
        descent_pct = (descent_m / 10.0) * descent_rate
        operation_margin_pct = hover_pct

        estimated_total_pct = horizontal_pct + climb_pct + descent_pct + operation_margin_pct
        expected_remaining_pct = battery_pct - estimated_total_pct

        preflight_battery_block = battery_pct < min_takeoff_pct and current_alt < 0.5

        if preflight_battery_block:
            verdict = "NOT_RECOMMENDED"
            verdict_ko = "이륙 기준 미달"
            color_hint = "#e74c3c"
            reason = (
                f"이 기체 프로파일은 이륙 전 배터리 {min_takeoff_pct:.0f}% 이상을 권장하지만, "
                f"현재 배터리는 {battery_pct:.1f}%입니다. 이륙 전 배터리 교체 또는 충전이 필요합니다."
            )
        elif expected_remaining_pct >= reserve_pct + 10:
            verdict = "SAFE"
            verdict_ko = "실행 가능"
            color_hint = "#2ecc71"
            reason = "목표 이동 후 HOME 복귀까지 고려해도 예비 배터리 기준보다 충분한 여유가 있습니다."
        elif expected_remaining_pct >= reserve_pct:
            verdict = "CAUTION"
            verdict_ko = "주의 필요"
            color_hint = "#f1c40f"
            reason = "예비 배터리 기준은 만족하지만 추가 여유가 크지 않습니다. 짧은 실습 또는 거리 축소를 권장합니다."
        else:
            verdict = "NOT_RECOMMENDED"
            verdict_ko = "실행 비추천"
            color_hint = "#e74c3c"
            reason = "목표 이동 후 HOME 복귀 시 예비 배터리 기준을 만족하기 어렵습니다."

        check = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "profile_name": profile.get("name", "Training Quad"),
            "battery_pct": round(battery_pct, 1),
            "reserve_pct": round(reserve_pct, 1),
            "min_takeoff_pct": round(min_takeoff_pct, 1),
            "hover_pct": round(hover_pct, 1),
            "alt_buffer_m": round(float(alt_buffer_m), 1),
            "horizontal_rate_pct_per_100m": horizontal_rate,
            "climb_rate_pct_per_10m": climb_rate,
            "descent_rate_pct_per_10m": descent_rate,
            "preflight_battery_block": preflight_battery_block,
            "start_lat": start_lat,
            "start_lon": start_lon,
            "target_lat": target_lat,
            "target_lon": target_lon,
            "home_lat": self.last_home_lat,
            "home_lon": self.last_home_lon,
            "current_alt_m": round(current_alt, 1),
            "target_alt_m": round(target_alt, 1),
            "recommended_alt_m": round(float(plan.get("recommended_alt_m", target_alt)), 1),
            "outbound_m": round(outbound_m, 1),
            "return_m": round(return_m, 1),
            "total_horizontal_m": round(total_horizontal_m, 1),
            "climb_m": round(climb_m, 1),
            "descent_m": round(descent_m, 1),
            "horizontal_pct": round(horizontal_pct, 1),
            "climb_pct": round(climb_pct, 1),
            "descent_pct": round(descent_pct, 1),
            "operation_margin_pct": round(operation_margin_pct, 1),
            "estimated_total_pct": round(estimated_total_pct, 1),
            "expected_remaining_pct": round(expected_remaining_pct, 1),
            "verdict": verdict,
            "verdict_ko": verdict_ko,
            "color_hint": color_hint,
            "reason": reason,
            "plan": plan
        }
        return check

    def format_battery_feasibility_check(self, check):
        lines = []
        lines.append("[Battery Safe Route Check]")
        lines.append(f"생성 시각: {check.get('created_at')}")
        lines.append("")
        lines.append(f"드론 프로파일: {check.get('profile_name')}")
        lines.append(f"현재 배터리: {check.get('battery_pct')}%")
        lines.append(f"최소 이륙 배터리 기준: {check.get('min_takeoff_pct')}%")
        lines.append(f"안전 예비 배터리: {check.get('reserve_pct')}%")
        lines.append(f"호버링/조작 여유: {check.get('operation_margin_pct')}%")
        lines.append("")
        lines.append(f"목표까지 거리: {check.get('outbound_m')}m")
        lines.append(f"목표 → HOME 복귀 거리: {check.get('return_m')}m")
        lines.append(f"총 수평 이동 거리: {check.get('total_horizontal_m')}m")
        lines.append("")
        lines.append(f"AI 추천 고도: {check.get('recommended_alt_m')}m")
        lines.append(f"Battery Safe 적용 목표 고도: {check.get('target_alt_m')}m")
        lines.append("고도 기준: HOME 기준 상대고도(AGL), MAV_FRAME_GLOBAL_RELATIVE_ALT_INT 사용")
        lines.append("주의: 절대고도(해발고도, AMSL)가 아닙니다.")
        lines.append(f"현재 상대고도: {check.get('current_alt_m')}m")
        lines.append(f"상승 필요 고도: {check.get('climb_m')}m")
        lines.append("")
        lines.append("[예상 배터리 소모]")
        lines.append(f"- 수평 이동 소모: {check.get('horizontal_pct')}%")
        lines.append(f"- 상승 소모: {check.get('climb_pct')}%")
        lines.append(f"- 하강 보정 소모: {check.get('descent_pct')}%")
        lines.append(f"- 호버링/조작 여유: {check.get('operation_margin_pct')}%")
        lines.append(f"- 총 예상 소모: {check.get('estimated_total_pct')}%")
        lines.append("")
        lines.append(f"예상 잔여 배터리: {check.get('expected_remaining_pct')}%")
        lines.append(f"판정: {check.get('verdict_ko')} ({check.get('verdict')})")
        lines.append(f"설명: {check.get('reason')}")
        lines.append("")
        lines.append("주의: 실제 배터리 소모는 바람, 기체 중량, 배터리 상태, 조종 패턴에 따라 달라집니다.")
        return "\n".join(lines)

    def show_battery_feasibility_check(self, check):
        result = self.format_battery_feasibility_check(check)
        self.update_command_explain(
            "Battery Safe Route 검증",
            "BATTERY_FEASIBILITY_GATE",
            "- 현재 배터리 잔량 확인\n- 목표 이동 거리 계산\n- HOME 복귀 거리 계산\n- 추천 고도 상승 비용 계산\n- 예비 배터리 기준 판정",
            result
        )
        self.add_practice_log(
            "BATTERY_SAFE_CHECK",
            "BATTERY_FEASIBILITY_GATE",
            check.get("verdict", "UNKNOWN"),
            f"battery={check.get('battery_pct')}%, estimated={check.get('estimated_total_pct')}%, remaining={check.get('expected_remaining_pct')}%, reserve={check.get('reserve_pct')}%",
            "PASS" if check.get("verdict") != "NOT_RECOMMENDED" else "FAIL"
        )
        self.set_status(f"Battery Safe 판정: {check.get('verdict_ko')} / 예상 잔여 {check.get('expected_remaining_pct')}%")

    def execute_ai_route_plan_with_battery_check(self, plan, check):
        """
        Battery Safe 검증 결과를 통과한 경우 AI Route를 실행한다.
        NOT_RECOMMENDED는 호출 이전 단계에서 차단된다.
        """
        if not plan:
            raise ValueError("실행할 AI Route 계획이 없습니다.")

        verdict = check.get("verdict")
        if verdict == "NOT_RECOMMENDED":
            messagebox.showwarning("Battery Safe 실행 차단", "배터리 검증 결과 실행 비추천 상태입니다.")
            return

        alt = float(check.get("target_alt_m", plan.get("recommended_alt_m")))
        safe_plan = dict(plan)
        safe_plan["recommended_alt_m"] = alt
        safe_plan["battery_check"] = check

        ok = messagebox.askyesno(
            "Battery Safe 실행 확인",
            f"Battery Safe 판정: {check.get('verdict_ko')}\n"
            f"목표 고도: {alt:.1f}m\n"
            f"예상 총 소모: {check.get('estimated_total_pct')}%\n"
            f"예상 잔여 배터리: {check.get('expected_remaining_pct')}%\n\n"
            "추천 고도 실행을 진행할까요?"
        )
        if not ok:
            self.add_practice_log("BATTERY_SAFE_EXECUTE", "SET_POSITION_TARGET_GLOBAL_INT", "CANCELLED", "사용자가 실행 취소")
            return

        self.add_practice_log(
            "BATTERY_SAFE_EXECUTE",
            "SET_POSITION_TARGET_GLOBAL_INT",
            "REQUESTED",
            f"verdict={verdict}, target_alt={alt}, estimated_total={check.get('estimated_total_pct')}%",
            "PASS"
        )
        self.execute_ai_route_plan(safe_plan)

    def format_ai_route_plan(self, plan):
        highest = plan.get("highest_building")
        lines = []
        lines.append("[AI Route Assist 분석 결과]")
        lines.append(f"생성 시각: {plan.get('created_at')}")
        lines.append(f"Ollama 사용: {'YES' if plan.get('ollama_used') else 'NO - 규칙 기반 fallback'}")
        if plan.get("ollama_error"):
            lines.append(f"Ollama 오류: {plan.get('ollama_error')}")
        lines.append("")
        lines.append(f"의도: {plan.get('intent')}")
        lines.append(f"우선순위: {', '.join(plan.get('priority', []))}")
        lines.append(f"LLM/규칙 설명: {plan.get('llm_explanation')}")
        lines.append("")
        lines.append(f"목표 좌표: {plan.get('target_lat'):.6f}, {plan.get('target_lon'):.6f}")
        lines.append(f"거리/방위각: {plan.get('distance_m')}m / {plan.get('bearing_deg')}°")
        lines.append(f"안전 여유고도: {plan.get('safety_margin_m')}m")
        lines.append(f"최소 안전 고도: {plan.get('min_safe_alt_m')}m")
        lines.append("고도 기준: HOME 기준 상대고도(AGL)")
        lines.append("MAVLink 프레임: MAV_FRAME_GLOBAL_RELATIVE_ALT_INT")
        lines.append("주의: 절대고도/해발고도(AMSL)가 아니라 이륙 지점 기준 높이입니다.")
        if highest:
            lines.append(f"가장 높은 경로 주변 건물: {highest['name']} / {highest['height_m']}m")
        else:
            lines.append("경로 주변 샘플 건물: 없음")
        lines.append("")
        lines.append("[고도 후보별 에너지 점수]")
        for c in plan.get("candidates", []):
            safe_text = "SAFE" if c.get("safe") else "UNSAFE"
            lines.append(f"- {c['alt_m']}m | 상승 {c['climb_m']}m | 점수 {c['energy_score']} | {safe_text}")
        lines.append("")
        lines.append(f"추천 고도: {plan.get('recommended_alt_m')}m")
        lines.append("추천 이유: 장애물 안전 여유를 만족하면서 교육용 에너지 점수가 가장 낮은 후보입니다.")
        lines.append("")
        lines.append("주의: 이 기능은 실습용 보조 계산입니다. 실제 장애물 회피/배터리 최적화 인증 기능이 아닙니다.")
        return "\n".join(lines)

    def show_ai_route_plan(self, plan):
        """AI 계획 결과를 COMMAND EXPLAIN 패널과 지도 TARGET에 반영."""
        try:
            lat = float(plan["target_lat"])
            lon = float(plan["target_lon"])
            if self.target_marker:
                try:
                    self.target_marker.delete()
                except Exception:
                    pass
            if self.target_icon:
                self.target_marker = self.map_widget.set_marker(lat, lon, text="AI TARGET", icon=self.target_icon)
            else:
                self.target_marker = self.map_widget.set_marker(lat, lon, text="AI TARGET")
        except Exception:
            pass

        result = self.format_ai_route_plan(plan)
        self.update_command_explain(
            "AI Route Assist 계획 생성",
            "OLLAMA_API + ROUTE_RULE_ENGINE",
            "- 목표 좌표 필요\n- 자연어 요청 입력\n- Ollama 또는 규칙 기반 의도 분석\n- 건물 높이 기반 안전고도 계산\n- 배터리 효율 추정 점수 계산",
            result
        )
        self.add_practice_log(
            "AI_ROUTE_PLAN",
            "OLLAMA_API + ROUTE_RULE_ENGINE",
            "CREATED",
            f"target=({plan.get('target_lat')},{plan.get('target_lon')}), recommended_alt={plan.get('recommended_alt_m')}, ollama={plan.get('ollama_used')}",
            "PASS"
        )

    def execute_ai_route_plan(self, plan):
        """사용자 승인 후 AI 추천 고도로 Guided 이동 명령 실행."""
        if not plan:
            raise ValueError("생성된 AI Route 계획이 없습니다.")

        if not self.check_basic_gate("AI Route Assist 실행", need_drone=True, need_gps=True, need_arm=True, need_airborne=True):
            messagebox.showwarning("AI Route 실행 불가", "COMMAND EXPLAIN 패널의 실패 원인과 해결 방법을 확인하세요.")
            return

        lat = float(plan["target_lat"])
        lon = float(plan["target_lon"])
        alt = float(plan["recommended_alt_m"])

        battery_msg = ""
        if plan.get("battery_check"):
            bc = plan.get("battery_check")
            battery_msg = (
                f"\nBattery Safe 판정: {bc.get('verdict_ko')}\n"
                f"예상 총 소모: {bc.get('estimated_total_pct')}%\n"
                f"예상 잔여 배터리: {bc.get('expected_remaining_pct')}%\n"
            )

        ok = messagebox.askyesno(
            "AI Route Assist 실행 확인",
            f"추천 고도 {alt:.1f}m로 목표 좌표까지 Guided 이동 명령을 전송할까요?\n\n"
            f"거리: {plan.get('distance_m')}m\n"
            f"최소 안전 고도: {plan.get('min_safe_alt_m')}m\n"
            f"{battery_msg}\n"
            f"주의: 이 기능은 실습용 보조 기능입니다."
        )
        if not ok:
            self.add_practice_log("AI_ROUTE_EXECUTE", "SET_POSITION_TARGET_GLOBAL_INT", "CANCELLED", "사용자가 실행 취소")
            return

        self.add_practice_log("AI_ROUTE_EXECUTE", "SET_POSITION_TARGET_GLOBAL_INT", "REQUESTED", f"alt={alt}, target=({lat},{lon})", "PASS")

        # AI Route는 추천 고도까지 먼저 상승한 뒤 목표 좌표로 이동한다.
        # 기존 send_guided_move_cmd는 목표 좌표와 고도를 한 번에 보내기 때문에,
        # HUD 상대고도가 바로 추천 고도로 보이지 않아 사용자가 혼동할 수 있었다.
        threading.Thread(
            target=self.execute_ai_route_with_altitude_stage,
            args=(lat, lon, alt),
            daemon=True
        ).start()

    def send_position_target_once(self, lat, lon, alt):
        """GUIDED 위치/고도 목표를 1회 전송한다. alt는 HOME 기준 상대고도(m)."""
        with self.mav_lock:
            self.drone.mav.set_position_target_global_int_send(  # type: ignore[union-attr]
                0,
                self.drone.target_system,  # type: ignore[union-attr]
                self.drone.target_component,  # type: ignore[union-attr]
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                0b110111111000,
                int(lat * 1e7),
                int(lon * 1e7),
                float(alt),
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0
            )

    def execute_ai_route_with_altitude_stage(self, target_lat, target_lon, target_alt):
        """
        AI Route 추천 고도 실행 전용 함수.

        동작:
        1. 현재 위치에서 추천 고도까지 먼저 상승 명령
        2. 추천 고도 근처에 도달하면 목표 좌표로 이동 명령
        3. 같은 목표를 몇 번 반복 전송하여 Guided setpoint 반영 안정성 확보

        HUD의 상대고도는 '현재 고도'이고 target_alt는 '목표 고도'라서
        명령 직후 숫자가 즉시 같아지지는 않는다.
        """
        try:
            if not self.check_basic_gate("AI Route Assist 실행", need_drone=True, need_gps=True, need_arm=True, need_airborne=True):
                return

            if self.d_mode != "GUIDED":
                self.set_drone_mode("GUIDED")
                time.sleep(0.5)

            start_lat = self.d_lat if self.d_lat else self.last_home_lat
            start_lon = self.d_lon if self.d_lon else self.last_home_lon

            # AI Route 추천 고도 실행 중에는 사용자가 지도를 다시 클릭해도
            # 기존 실행 경로가 사라지지 않도록 별도 실행 경로선을 고정한다.
            self.after(0, lambda: self.set_active_route_visual(
                target_lat,
                target_lon,
                target_alt,
                "AI Route 실행 경로"
            ))

            current_alt = float(self.d_alt)
            target_alt = float(target_alt)

            self.after(0, lambda: self.update_command_explain(
                "AI Route 고도 상승 단계",
                "SET_POSITION_TARGET_GLOBAL_INT",
                "- 현재 위치 유지\n- 추천 고도까지 먼저 상승\n- 추천 고도 근처 도달 후 목표 좌표 이동",
                f"현재 상대고도: {current_alt:.1f}m\n"
                f"추천 목표고도: {target_alt:.1f}m\n\n"
                f"HUD의 상대고도는 현재 고도입니다. "
                f"추천 고도 실행 직후 바로 {target_alt:.1f}m가 되는 것이 아니라, "
                f"드론이 상승하면서 점진적으로 바뀝니다.\n\n"
                f"이 목표고도는 HOME 기준 상대고도(AGL)이며, 절대고도/해발고도가 아닙니다."
            ))
            self.set_status(f"AI Route 1단계: 추천 고도 {target_alt:.1f}m까지 상승 명령 전송 중...")

            # 1단계: 현재 위치에서 추천 고도까지 상승
            # 이미 목표 고도 근처이면 바로 이동 단계로 넘어간다.
            climb_deadline = time.time() + 25
            while self.running and not self.stop_event.is_set():
                if self.d_alt >= target_alt - 1.5:
                    break

                self.send_position_target_once(start_lat, start_lon, target_alt)
                self.set_status(f"추천 고도 상승 중... 현재 {self.d_alt:.1f}m / 목표 {target_alt:.1f}m")
                time.sleep(1.0)

                if time.time() > climb_deadline:
                    self.set_status("추천 고도 도달 대기 시간이 초과되어 목표 좌표 이동을 계속 진행합니다.")
                    break

            # 2단계: 목표 좌표로 이동
            self.after(0, lambda: self.update_command_explain(
                "AI Route 목표 이동 단계",
                "SET_POSITION_TARGET_GLOBAL_INT",
                "- 추천 고도 유지\n- AI TARGET 좌표로 Guided 이동\n- 목표 고도와 목표 좌표를 함께 전송",
                f"목표 좌표: {target_lat:.6f}, {target_lon:.6f}\n"
                f"목표 고도: {target_alt:.1f}m\n"
                f"현재 고도: {self.d_alt:.1f}m\n\n"
                f"목표 이동 명령을 반복 전송하여 Guided 목표값 반영을 안정화합니다."
            ))
            self.set_status(f"AI Route 2단계: 목표 좌표로 이동 명령 전송 중... 목표고도 {target_alt:.1f}m")

            for _ in range(8):
                if not self.running or self.stop_event.is_set():
                    return
                self.send_position_target_once(target_lat, target_lon, target_alt)
                time.sleep(0.5)

            distance_m = self.calc_distance_m(self.d_lat, self.d_lon, target_lat, target_lon) if self.d_lat and self.d_lon else 0.0
            self.set_status(f"AI Route 목표 이동 명령 전송 완료: 목표고도 {target_alt:.1f}m")
            self.add_waypoint_record("AI_ROUTE_MOVE", target_lat, target_lon, target_alt, "EXECUTED", "AI_ROUTE")
            self.add_practice_log(
                "AI_ROUTE_GUIDED_MOVE",
                "SET_POSITION_TARGET_GLOBAL_INT",
                "SENT",
                f"target_lat={target_lat}, target_lon={target_lon}, target_alt={target_alt}, current_alt={self.d_alt:.1f}, remaining_distance_m={distance_m:.1f}",
                "PASS"
            )

        except Exception as e:
            self.explain_failure(
                "AI Route 실행 실패",
                "SET_POSITION_TARGET_GLOBAL_INT",
                f"AI 추천 고도/목표 이동 명령 실행 중 오류가 발생했습니다: {e}",
                "1. GUIDED 모드인지 확인하세요.\n2. ARM 상태와 GPS 상태를 확인하세요.\n3. 목표 좌표와 추천 고도가 정상인지 확인하세요.",
                "AI_ROUTE_EXECUTE"
            )
            print(f"[AI ROUTE EXECUTE ERROR] {e}")

    # -----------------------------------------------------------
    # 버튼 / MAVLink 명령
    # -----------------------------------------------------------
    def toggle_sitl(self):
        if self.sitl.proc is None:
            self.start_sitl_thread()
        else:
            self.stop_sitl_action()

    def set_drone_mode(self, mode_name):
        if not self.check_basic_gate("비행 모드 변경", need_drone=True):
            return

        try:
            with self.mav_lock:
                self.drone.set_mode(mode_name.upper())  # type: ignore[union-attr]

            self.status_var.set(f"모드 변경 명령 전송: {mode_name}")
            self.update_command_explain(
                "비행 모드 변경",
                "SET_MODE",
                "- 드론 연결 필요\n- 해당 모드가 ArduPilot에서 지원되어야 함",
                f"{mode_name} 모드 변경 명령을 전송했습니다.\n\n초보자 해설:\n{self.get_mode_learning_text(mode_name)}"
            )
            self.add_practice_log("MODE_CHANGE", "SET_MODE", "SENT", f"mode={mode_name}")
            self.update_beginner_guide()
            print(f"[명령] 비행 모드 변경 시도: {mode_name}")

        except Exception as e:
            self.explain_failure(
                "비행 모드 변경 실패",
                "SET_MODE",
                f"모드 변경 명령 전송 중 오류가 발생했습니다: {e}",
                "1. MAVLink 연결이 유지되는지 확인하세요.\n2. 현재 기체가 해당 모드를 지원하는지 확인하세요.\n3. SITL을 재시작한 뒤 다시 시도하세요.",
                "MODE_CHANGE"
            )
            print(f"[명령 에러] 모드 변경 실패: {e}")

    def get_mode_learning_text(self, mode_name):
        mode_name = str(mode_name).strip().upper()
        mode_help = {
            "STABILIZE": "기본 수동 안정화 모드입니다. 초보자는 조이스틱 입력에 드론이 어떻게 반응하는지 이해할 때 사용합니다.",
            "ALT_HOLD": "고도를 유지하면서 수동 조종하는 모드입니다. 조이스틱 연습에 적합합니다.",
            "LOITER": "GPS를 사용해 현재 위치를 유지하는 모드입니다. 초보자 조이스틱 연습에 가장 무난합니다.",
            "GUIDED": "GCS가 목표 좌표를 보내면 드론이 해당 위치로 이동하는 모드입니다. 지도 클릭 이동과 TAKEOFF에 적합합니다.",
            "AUTO": "미리 작성한 미션을 자동 수행하는 모드입니다.",
            "RTL": "홈 위치로 자동 복귀하는 모드입니다. 실습 종료 또는 복귀 상황에서 사용합니다.",
            "LAND": "현재 위치에서 자동 착륙하는 모드입니다."
        }
        return mode_help.get(mode_name, "선택한 비행 모드의 동작 조건을 확인해야 합니다.")

    def cmd_arm(self):
        if not self.check_basic_gate("ARM / 시동", need_drone=True, need_gps=False):
            messagebox.showwarning("시동 불가", "드론이 연결되지 않았습니다.")
            return

        allowed_modes = ["STABILIZE", "LOITER", "GUIDED", "ALT_HOLD", "POSHOLD"]
        current_mode = self.d_mode.upper()

        if current_mode not in allowed_modes:
            self.explain_failure(
                "ARM / 시동 차단",
                "MAV_CMD_COMPONENT_ARM_DISARM",
                f"현재 '{current_mode}' 모드에서는 초보자 실습 기준상 ARM을 권장하지 않습니다.",
                "1. GUIDED, LOITER, ALT_HOLD 중 하나로 변경하세요.\n2. 모드 변경 후 ARM / 시동 버튼을 다시 누르세요.",
                "ARM"
            )
            messagebox.showwarning(
                "시동 불가",
                f"현재 '{current_mode}' 모드에서는 시동을 걸 수 없습니다.\n"
                "STABILIZE, LOITER, GUIDED 등으로 모드를 변경해 주세요."
            )
            return

        with self.mav_lock:
            self.drone.mav.command_long_send(  # type: ignore[union-attr]
                self.drone.target_system,  # type: ignore[union-attr]
                self.drone.target_component,  # type: ignore[union-attr]
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,
                1,
                0,
                0,
                0,
                0,
                0,
                0
            )

        self.force_ui_armed = True
        self.d_is_armed = True
        self.update_command_explain(
            "ARM / 시동",
            "MAV_CMD_COMPONENT_ARM_DISARM",
            "- 드론 연결 필요\n- STABILIZE/LOITER/GUIDED/ALT_HOLD/POSHOLD 계열 모드 권장",
            f"{current_mode} 모드에서 ARM 명령을 전송했습니다.\n\n초보자 해설:\nARM은 모터 시동이 가능한 상태로 전환하는 단계입니다. 다음 단계는 TAKEOFF입니다."
        )
        self.add_practice_log("ARM", "MAV_CMD_COMPONENT_ARM_DISARM", "SENT", f"mode={current_mode}", "PASS")
        self.update_beginner_guide()
        print(f"[명령] {current_mode} 모드에서 시동(ARM) 명령 전송")

    def cmd_disarm(self):
        if self.drone:
            with self.mav_lock:
                self.drone.mav.command_long_send(  # type: ignore[union-attr]
                    self.drone.target_system,  # type: ignore[union-attr]
                    self.drone.target_component,  # type: ignore[union-attr]
                    mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0
                )
            self.force_ui_armed = False
            self.d_is_armed = False
            self.update_command_explain(
                "DISARM / 해제",
                "MAV_CMD_COMPONENT_ARM_DISARM",
                "- 드론 연결 필요",
                "DISARM 명령을 전송했습니다."
            )
            self.add_practice_log("DISARM", "MAV_CMD_COMPONENT_ARM_DISARM", "SENT", "param1=0")
            self.update_beginner_guide()
            print("[명령] 시동 해제(DISARM) 명령 전송")
        else:
            self.status_var.set("DISARM 실패: 드론이 연결되지 않았습니다.")
            self.add_practice_log("DISARM", "MAV_CMD_COMPONENT_ARM_DISARM", "BLOCKED", "드론 미연결", "FAIL: no drone connection")

    def check_takeoff_battery_gate(self):
        """이륙 전 드론 프로파일 기반 최소 배터리 기준 검사"""
        try:
            min_pct = float(self.drone_profile.get("min_takeoff_battery_pct", 70.0))
            current_pct = float(self.d_bat_p)

            if current_pct <= 0:
                self.update_command_explain(
                    "TAKEOFF 배터리 기준 확인",
                    "BATTERY_PREFLIGHT_GATE",
                    "- 배터리 텔레메트리 수신 대기\n- 값이 0%이면 SITL 초기화 중일 수 있음",
                    "현재 배터리 퍼센트 값이 아직 정상 수신되지 않았습니다.\n"
                    "실제 검증에서는 배터리 값 수신 후 이륙 기준을 다시 확인해야 합니다."
                )
                return True

            if current_pct < min_pct and self.d_alt < 0.5:
                self.explain_failure(
                    "TAKEOFF 차단",
                    "BATTERY_PREFLIGHT_GATE",
                    f"현재 배터리 {current_pct:.1f}%가 최소 이륙 기준 {min_pct:.0f}%보다 낮습니다.",
                    "1. 배터리를 교체하거나 충전하세요.\n"
                    "2. 본 교육용 드론 프로파일은 이륙 전 70% 이상을 기준으로 설정되어 있습니다.\n"
                    "3. Battery Safe 모드에서 경로 실행 가능성을 먼저 확인하세요.",
                    "TAKEOFF_BATTERY_GATE"
                )
                return False

            return True

        except Exception as e:
            print(f"[TAKEOFF BATTERY GATE ERROR] {e}")
            return True

    def cmd_takeoff_popup(self):
        if not self.check_basic_gate("TAKEOFF", need_drone=True, need_gps=True, need_arm=True):
            messagebox.showwarning("이륙 불가", "COMMAND EXPLAIN 패널의 실패 원인과 해결 방법을 확인하세요.")
            return

        if not self.check_takeoff_battery_gate():
            messagebox.showwarning("이륙 불가", "배터리 기준 미달입니다. COMMAND EXPLAIN 패널을 확인하세요.")
            return

        self.update_command_explain(
            "TAKEOFF 준비",
            "MAV_CMD_NAV_TAKEOFF",
            "- 드론 연결\n- GPS 안정화\n- ARM 상태\n- 목표 고도 입력 필요",
            "이륙 고도 입력 팝업을 표시했습니다.\n\n초보자 해설:\nTAKEOFF는 지상 상태의 드론을 지정한 상대고도까지 올리는 명령입니다."
        )
        self.add_practice_log("TAKEOFF_POPUP", "MAV_CMD_NAV_TAKEOFF", "OPENED", "이륙 고도 입력 팝업")
        TakeoffDialog(self, self.send_takeoff_cmd)

    def send_takeoff_cmd(self, altitude):
        if altitude <= 0:
            self.explain_failure(
                "TAKEOFF 차단",
                "MAV_CMD_NAV_TAKEOFF",
                "목표 고도가 0m 이하입니다.",
                "1. 1m 이상의 목표 고도를 입력하세요.\n2. 초보자 실습에서는 5~10m 정도를 권장합니다.",
                "TAKEOFF"
            )
            return

        if not self.check_basic_gate("TAKEOFF", need_drone=True, need_gps=True, need_arm=True):
            return

        if self.d_mode != "GUIDED":
            messagebox.showinfo("모드 변경", "TAKEOFF 실행을 위해 GUIDED로 변경하겠습니다.")
            self.set_drone_mode("GUIDED")
            time.sleep(0.5)

        try:
            with self.mav_lock:
                self.drone.mav.command_long_send(  # type: ignore[union-attr]
                    self.drone.target_system,  # type: ignore[union-attr]
                    self.drone.target_component,  # type: ignore[union-attr]
                    mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    altitude
                )

            self.status_var.set(f"이륙 명령 전송: 고도 {altitude}m")
            self.update_command_explain(
                "TAKEOFF 실행",
                "MAV_CMD_NAV_TAKEOFF",
                "- 드론 연결\n- GPS 안정화\n- GUIDED 모드\n- ARM 상태\n- 목표 고도 입력",
                f"고도 {altitude}m 이륙 명령을 전송했습니다.\n\n초보자 해설:\n이제 상대고도 값이 올라가는지 HUD에서 확인하세요. 이륙 후에는 지도 클릭 이동 또는 조이스틱 실습을 진행할 수 있습니다."
            )
            self.add_practice_log("TAKEOFF", "MAV_CMD_NAV_TAKEOFF", "SENT", f"altitude={altitude}", "PASS")
            self.update_beginner_guide()
            print(f"[명령] 이륙 명령 전송 (고도: {altitude}m)")

        except Exception as e:
            self.explain_failure(
                "TAKEOFF 실패",
                "MAV_CMD_NAV_TAKEOFF",
                f"이륙 명령 전송 중 오류가 발생했습니다: {e}",
                "1. MAVLink 연결을 확인하세요.\n2. GUIDED 모드와 ARM 상태를 다시 확인하세요.\n3. SITL을 재시작한 뒤 다시 시도하세요.",
                "TAKEOFF"
            )

    def cmd_battery_reset(self):
        if self.drone:
            if self.d_is_armed:
                confirm = messagebox.askyesno(
                    "비행 중 초기화 경고",
                    "현재 드론이 시동(ARM) 상태입니다.\n\n"
                    "비행 중에 배터리를 100%로 강제 초기화하시겠습니까?\n"
                    "(시뮬레이터 환경에서만 권장됩니다.)"
                )
            else:
                confirm = messagebox.askyesno(
                    "배터리 초기화",
                    "배터리 소모량을 리셋하고 100%로 다시 채우시겠습니까?"
                )

            if confirm:
                with self.mav_lock:
                    try:
                        battery_reset_cmd = getattr(mavutil.mavlink, "MAV_CMD_BATTERY_RESET", 502)
                        self.drone.mav.command_long_send(  # type: ignore[union-attr]
                            self.drone.target_system,  # type: ignore[union-attr]
                            self.drone.target_component,  # type: ignore[union-attr]
                            battery_reset_cmd,
                            0,
                            1,
                            100,
                            0,
                            0,
                            0,
                            0,
                            0
                        )
                        self.status_var.set("배터리 잔량 명령 전송 완료")
                        self.update_command_explain(
                            "배터리 잔량 초기화",
                            "MAV_CMD_BATTERY_RESET",
                            "- SITL 또는 테스트 환경 권장\n- 실제 비행체에서는 신중히 사용",
                            "배터리 잔량 초기화 명령을 전송했습니다."
                        )
                        self.add_practice_log("BATTERY_RESET", "MAV_CMD_BATTERY_RESET", "SENT", "battery=100%")
                        print("[명령] 배터리를 100% 상태로 초기화했습니다.")
                    except Exception as e:
                        self.add_practice_log("BATTERY_RESET", "MAV_CMD_BATTERY_RESET", "ERROR", str(e))
                        print(f"[명령 에러] 배터리 초기화 실패: {e}")
        else:
            self.status_var.set("배터리 초기화 실패: 드론이 연결되지 않았습니다.")
            self.add_practice_log("BATTERY_RESET", "MAV_CMD_BATTERY_RESET", "BLOCKED", "드론 미연결", "FAIL: no drone connection")

    def calc_distance_m(self, lat1, lon1, lat2, lon2):
        """두 GPS 좌표 사이의 거리(m)를 계산한다."""
        try:
            r = 6371000.0
            p1 = math.radians(lat1)
            p2 = math.radians(lat2)
            dp = math.radians(lat2 - lat1)
            dl = math.radians(lon2 - lon1)
            a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return r * c
        except Exception:
            return 0.0

    def calc_bearing_deg(self, lat1, lon1, lat2, lon2):
        """현재 위치에서 목표 좌표까지의 방위각(deg)을 계산한다."""
        try:
            p1 = math.radians(lat1)
            p2 = math.radians(lat2)
            dl = math.radians(lon2 - lon1)
            y = math.sin(dl) * math.cos(p2)
            x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
            brng = math.degrees(math.atan2(y, x))
            return (brng + 360) % 360
        except Exception:
            return 0.0

    # -----------------------------------------------------------
    # 지도 마우스 클릭 기반 Guided 이동
    # -----------------------------------------------------------
    def on_map_left_click(self, coordinate):
        if self.drone is None or self.sitl.proc is None:
            return

        # 지도 좌상단 확대/축소 버튼 영역 클릭은 이동 명령에서 제외
        try:
            x_rel = self.map_widget.canvas.winfo_pointerx() - self.map_widget.canvas.winfo_rootx()
            y_rel = self.map_widget.canvas.winfo_pointery() - self.map_widget.canvas.winfo_rooty()
            if x_rel < 80 and y_rel < 120:
                return
        except Exception:
            pass

        if not self.check_basic_gate("지도 클릭 이동", need_drone=True, need_gps=True, need_arm=True):
            messagebox.showinfo("이동 불가", "COMMAND EXPLAIN 패널의 실패 원인과 해결 방법을 확인하세요.")
            return

        lat, lon = coordinate

        if self.target_marker:
            try:
                self.target_marker.delete()
            except Exception:
                pass

        if self.target_icon:
            self.target_marker = self.map_widget.set_marker(lat, lon, text="TARGET", icon=self.target_icon)
        else:
            self.target_marker = self.map_widget.set_marker(lat, lon, text="TARGET")

        distance_m = self.calc_distance_m(self.d_lat, self.d_lon, lat, lon) if self.d_lat and self.d_lon else 0.0
        bearing_deg = self.calc_bearing_deg(self.d_lat, self.d_lon, lat, lon) if self.d_lat and self.d_lon else 0.0

        self.update_command_explain(
            "GUIDED 이동 목표 지정",
            "SET_POSITION_TARGET_GLOBAL_INT",
            "- 드론 ARM 상태 필요\n- GPS 안정화 필요\n- 지도 목표 좌표 선택\n- 이동 고도 입력 필요",
            f"목표 좌표를 지정했습니다.\nlat={lat:.6f}, lon={lon:.6f}\n현재 위치 기준 거리={distance_m:.1f}m, 방위각={bearing_deg:.1f}°\n\n초보자 해설:\n지도 클릭은 즉시 이동이 아니라 목표 좌표 지정 단계입니다. 다음 팝업에서 목표 고도를 입력하면 실제 이동 명령이 전송됩니다."
        )
        self.add_practice_log("TARGET_SELECTED", "MAP_CLICK", "SELECTED", f"target_lat={lat}, target_lon={lon}, distance_m={distance_m:.1f}")
        self.add_waypoint_record("WAYPOINT", lat, lon, "", "SELECTED", "MAP_CLICK")
        self.update_beginner_guide()
        GuidedMoveDialog(self, self.send_guided_move_cmd, lat, lon)

    def send_guided_move_cmd(self, lat, lon, alt):
        if alt <= 0:
            self.explain_failure(
                "GUIDED 이동 차단",
                "SET_POSITION_TARGET_GLOBAL_INT",
                "목표 고도가 0m 이하입니다.",
                "1. 1m 이상의 목표 고도를 입력하세요.\n2. 초보자 실습에서는 현재 고도와 비슷한 값을 권장합니다.",
                "GUIDED_MOVE"
            )
            return

        if not self.check_basic_gate("GUIDED 이동", need_drone=True, need_gps=True, need_arm=True, need_airborne=True):
            messagebox.showwarning("이동 불가", "COMMAND EXPLAIN 패널의 실패 원인과 해결 방법을 확인하세요.")
            return

        if self.d_mode != "GUIDED":
            messagebox.showinfo("안내", "GUIDED 이동을 위해 GUIDED 모드로 전환합니다.")
            self.set_drone_mode("GUIDED")
            time.sleep(0.3)

        distance_m = self.calc_distance_m(self.d_lat, self.d_lon, lat, lon) if self.d_lat and self.d_lon else 0.0
        risk_text = "낮음" if distance_m <= 50 else "중간" if distance_m <= 150 else "높음"

        with self.mav_lock:
            try:
                self.drone.mav.set_position_target_global_int_send(  # type: ignore[union-attr]
                    0,
                    self.drone.target_system,  # type: ignore[union-attr]
                    self.drone.target_component,  # type: ignore[union-attr]
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                    0b110111111000,
                    int(lat * 1e7),
                    int(lon * 1e7),
                    float(alt),
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0
                )
                self.set_active_route_visual(lat, lon, alt, "GUIDED 이동 실행 경로")
                self.status_var.set("목표지점 이동 명령 전송 완료")
                self.update_command_explain(
                    "GUIDED 이동 실행",
                    "SET_POSITION_TARGET_GLOBAL_INT",
                    "- 드론 연결\n- GPS 안정화\n- ARM 상태\n- 상대고도 0.5m 이상\n- GUIDED 모드",
                    f"목표 좌표로 이동 명령을 전송했습니다.\nlat={lat:.6f}, lon={lon:.6f}, alt={alt}m\n거리={distance_m:.1f}m, 위험도={risk_text}\n\n초보자 해설:\nSET_POSITION_TARGET_GLOBAL_INT는 GCS가 목표 GPS 좌표와 상대고도를 보내는 Guided 이동 명령입니다."
                )
                self.update_last_waypoint_status("GUIDED_MOVE", alt, "EXECUTED", "GUIDED")
                self.add_practice_log("GUIDED_MOVE", "SET_POSITION_TARGET_GLOBAL_INT", "SENT", f"lat={lat}, lon={lon}, alt={alt}, distance_m={distance_m:.1f}, risk={risk_text}", "PASS")
                self.update_beginner_guide()
            except Exception as e:
                self.explain_failure(
                    "GUIDED 이동 실패",
                    "SET_POSITION_TARGET_GLOBAL_INT",
                    f"드론 이동 명령 전송 중 오류가 발생했습니다: {e}",
                    "1. MAVLink 연결을 확인하세요.\n2. GUIDED 모드와 ARM 상태를 다시 확인하세요.\n3. 목표 고도와 좌표를 다시 입력하세요.",
                    "GUIDED_MOVE"
                )
                print(f"[드론 이동 에러] {e}")

    def on_center_mode_change(self):
        center_current_mode = self.map_center_mode.get()

        if center_current_mode == 2 and self.last_center_mode == 2:
            self.map_center_mode.set(0)
            center_current_mode = 0

        if center_current_mode == 0:
            self.status_var.set("지도 자동 이동이 비활성화되었습니다. 지도를 자유롭게 조작할 수 있습니다.")
            self.add_practice_log("MAP_CENTER_MODE", "UI", "CHANGED", "지도 이동 없음")

        elif center_current_mode == 1:
            try:
                lat = float(self.lat_entry.get())
                lon = float(self.lon_entry.get())
                self.map_widget.set_position(lat, lon)
                self.status_var.set("지도를 홈 마커 중심으로 정렬했습니다.")
                self.add_practice_log("MAP_CENTER_MODE", "UI", "CHANGED", "홈 마커 중앙")
            except Exception:
                pass

        elif center_current_mode == 2:
            if self.gps_enable_time and time.time() > self.gps_enable_time:
                self.map_widget.set_position(self.d_lat, self.d_lon)
                self.status_var.set("실시간 드론 추적 중앙 고정이 활성화되었습니다.")

        self.last_center_mode = center_current_mode

    def create_virtual_joystick(self):
        self.js_title = tk.Label(
            self.js_panel_frame,
            text="가상 조이스틱 (Mode 2)",
            fg="white",
            bg="#2c3e50",
            font=("맑은 고딕", 10, "bold")
        )
        self.js_title.pack(pady=(5, 0))

        self.js_canvas = tk.Canvas(
            self.js_panel_frame,
            width=280,
            height=130,
            bg="#1c1c1c",
            highlightthickness=1,
            highlightbackground="#34495e"
        )
        self.js_canvas.pack(padx=5, pady=5)

        self.lx, self.ly = 65, 65
        self.rx, self.ry = 215, 65
        self.js_radius = 45

        self.js_canvas.create_oval(
            self.lx - self.js_radius,
            self.ly - self.js_radius,
            self.lx + self.js_radius,
            self.ly + self.js_radius,
            outline="#34495e",
            width=2
        )
        self.js_canvas.create_oval(
            self.rx - self.js_radius,
            self.ry - self.js_radius,
            self.rx + self.js_radius,
            self.ry + self.js_radius,
            outline="#34495e",
            width=2
        )

        self.js_canvas.create_line(self.lx - 10, self.ly, self.lx + 10, self.ly, fill="#34495e")
        self.js_canvas.create_line(self.lx, self.ly - 10, self.lx, self.ly + 10, fill="#34495e")
        self.js_canvas.create_line(self.rx - 10, self.ry, self.rx + 10, self.ry, fill="#34495e")
        self.js_canvas.create_line(self.rx, self.ry - 10, self.rx, self.ry + 10, fill="#34495e")

        self.left_knob = self.js_canvas.create_oval(
            self.lx - 12,
            self.ly - 12,
            self.lx + 12,
            self.ly + 12,
            fill="#e67e22",
            outline="white"
        )
        self.right_knob = self.js_canvas.create_oval(
            self.rx - 12,
            self.ry - 12,
            self.rx + 12,
            self.ry + 12,
            fill="#3498db",
            outline="white"
        )

        self.js_canvas.create_text(
            self.lx,
            self.ly + 52,
            text="스로틀 / 요",
            fill="#95a5a6",
            font=("Consolas", 8, "bold")
        )
        self.js_canvas.create_text(
            self.rx,
            self.ry + 52,
            text="피치 / 롤",
            fill="#95a5a6",
            font=("Consolas", 8, "bold")
        )

        self.js_canvas.bind("<B1-Motion>", self.on_joystick_drag)
        self.js_canvas.bind("<ButtonRelease-1>", self.on_joystick_release)
        self.js_panel_frame.place_forget()

    def on_joystick_drag(self, event):
        x, y = event.x, event.y

        if x < 140:
            dx, dy = x - self.lx, y - self.ly
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > self.js_radius:
                dx, dy = (dx / dist) * self.js_radius, (dy / dist) * self.js_radius

            self.js_canvas.coords(
                self.left_knob,
                self.lx + dx - 12,
                self.ly + dy - 12,
                self.lx + dx + 12,
                self.ly + dy + 12
            )

            self.js_throttle = int(1500 - (dy / self.js_radius) * 500)
            self.js_yaw = int(1500 + (dx / self.js_radius) * 500)

        else:
            dx, dy = x - self.rx, y - self.ry
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > self.js_radius:
                dx, dy = (dx / dist) * self.js_radius, (dy / dist) * self.js_radius

            self.js_canvas.coords(
                self.right_knob,
                self.rx + dx - 12,
                self.ry + dy - 12,
                self.rx + dx + 12,
                self.ry + dy + 12
            )

            self.js_pitch = int(1500 + (dy / self.js_radius) * 500)
            self.js_roll = int(1500 + (dx / self.js_radius) * 500)

    def on_joystick_release(self, _event):
        self.js_canvas.coords(self.left_knob, self.lx - 12, self.ly - 12, self.lx + 12, self.ly + 12)
        self.js_canvas.coords(self.right_knob, self.rx - 12, self.ry - 12, self.rx + 12, self.ry + 12)

        self.js_roll = 1500
        self.js_pitch = 1500
        self.js_throttle = 1500
        self.js_yaw = 1500

    def toggle_joystick_view(self):
        if self.joystick_visible.get():
            if not self.check_basic_gate("조이스틱 활성화", need_drone=True, need_arm=True):
                ToastMessage(self, "조이스틱 활성화가 차단되었습니다. COMMAND EXPLAIN 패널에서 원인과 해결 방법을 확인하세요.", duration=4000)
                self.joystick_visible.set(False)
                self.js_panel_frame.place_forget()
                return

            current_m = self.d_mode.strip().upper()
            if current_m == "GUIDED":
                self.js_roll = 1500
                self.js_pitch = 1500
                self.js_throttle = 1500
                self.js_yaw = 1500

                self.clear_target_marker_and_path()

                ToastMessage(self, "LOITER 모드로 자동 전환합니다.\n조이스틱을 초기화시키고 LOITER 모드로 전환합니다.", duration=4000)
                self.d_mode = "LOITER"
                self.set_drone_mode("LOITER")
                time.sleep(0.2)

                self.js_panel_frame.place(relx=0.5, rely=1.0, y=-10, anchor="s")
                self.update_command_explain(
                    "조이스틱 활성화",
                    "RC_CHANNELS_OVERRIDE",
                    "- 드론 연결\n- ARM 상태\n- LOITER 또는 ALT_HOLD 권장",
                    "GUIDED 모드에서 LOITER 모드로 전환한 뒤 조이스틱을 활성화했습니다.\n\n초보자 해설:\n왼쪽 스틱은 Throttle/Yaw, 오른쪽 스틱은 Pitch/Roll 입력입니다."
                )
                print("[INFO] 가상 조이스틱 인터페이스가 지도 중앙 하단에 활성화되었습니다.")
                self.add_practice_log("JOYSTICK", "RC_CHANNELS_OVERRIDE", "ENABLED", f"mode={current_m}")
                self.update_beginner_guide()
                return

            if current_m not in ["LOITER", "ALT_HOLD"]:
                self.explain_failure(
                    "조이스틱 활성화 차단",
                    "RC_CHANNELS_OVERRIDE",
                    f"현재 모드가 {current_m}입니다. 초보자 조이스틱 실습은 LOITER 또는 ALT_HOLD에서 권장됩니다.",
                    "1. FLIGHT MODE에서 LOITER 또는 ALT_HOLD를 선택하세요.\n2. 모드 변경 후 조이스틱 활성화를 다시 체크하세요.",
                    "JOYSTICK"
                )
                ToastMessage(self, "안전한 수동 조종을 위해 우측 패널에서 LOITER 또는 ALT_HOLD 모드로 변경한 뒤 다시 활성화해 주세요.", duration=4000)
                self.joystick_visible.set(False)
                self.js_panel_frame.place_forget()
                return

            self.js_panel_frame.place(relx=0.5, rely=1.0, y=-10, anchor="s")
            self.update_command_explain(
                "조이스틱 활성화",
                "RC_CHANNELS_OVERRIDE",
                "- 드론 연결\n- ARM 상태\n- LOITER 또는 ALT_HOLD 모드",
                "조이스틱 입력을 RC override 명령으로 전송할 준비가 되었습니다.\n\n초보자 해설:\n스틱을 크게 움직이기 전에 작은 입력부터 확인하세요."
            )
            print("[INFO] 가상 조이스틱 인터페이스가 지도 중앙 하단에 활성화되었습니다.")
            self.add_practice_log("JOYSTICK", "RC_CHANNELS_OVERRIDE", "ENABLED", f"mode={current_m}")
            self.update_beginner_guide()

        else:
            self.js_panel_frame.place_forget()
            if self.drone:
                try:
                    with self.mav_lock:
                        self.drone.mav.rc_channels_override_send(  # type: ignore[union-attr]
                            self.drone.target_system,  # type: ignore[union-attr]
                            self.drone.target_component,  # type: ignore[union-attr]
                            0, 0, 0, 0, 0, 0, 0, 0
                        )
                    self.update_command_explain(
                        "조이스틱 비활성화",
                        "RC_CHANNELS_OVERRIDE",
                        "- 조이스틱 활성 상태",
                        "RC override 입력을 해제했습니다.\n\n초보자 해설:\n조이스틱을 끄면 GCS가 보내던 수동 입력이 중단됩니다."
                    )
                    print("[MAVLink] 조이스틱 비활성화: RC 제어권을 기체 시스템으로 안전하게 이관했습니다.")
                    self.add_practice_log("JOYSTICK", "RC_CHANNELS_OVERRIDE", "DISABLED", "RC override release")
                    self.update_beginner_guide()
                except Exception:
                    pass

    def js_loop(self):
        if self.running:
            if self.joystick_visible.get() and self.drone:
                try:
                    with self.mav_lock:
                        self.drone.mav.rc_channels_override_send(  # type: ignore[union-attr]
                            self.drone.target_system,  # type: ignore[union-attr]
                            self.drone.target_component,  # type: ignore[union-attr]
                            self.js_roll,
                            self.js_pitch,
                            self.js_throttle,
                            self.js_yaw,
                            0, 0, 0, 0
                        )
                except Exception as e:
                    print("[JOYSTICK SEND ERROR]", e)

            self.after(100, self.js_loop)

    # -----------------------------------------------------------
    # Start / Exit SITL
    # -----------------------------------------------------------
    def start_sitl_thread(self):
        self.add_practice_log("START_SITL_CLICK", "SYSTEM", "REQUESTED", "Start SITL 버튼 클릭")
        self.update_command_explain(
            "SITL 시작",
            "ArduCopter.exe + MAVLink TCP 5760",
            "- sitl/ArduCopter.exe 경로 필요\n- LAT/LON 입력값 필요",
            "SITL 시작 작업을 백그라운드 스레드로 요청했습니다."
        )
        threading.Thread(target=self.start_sitl, daemon=True).start()

    def start_sitl(self):
        if self.sitl.proc:
            return

        self.config(cursor="watch")
        self.map_widget.config(cursor="watch")
        self.right_panel.config(cursor="watch")
        self.update_idletasks()
        self.start_btn.config(state="disabled")

        self.after(0, lambda: self.status_var.set("SITL 프로세스 시작 중..."))

        if self.home_marker:
            self.home_marker.delete()
            self.home_marker = None

        self.clear_target_marker_and_path()

        lat = float(self.lat_entry.get())
        lon = float(self.lon_entry.get())
        self.last_home_lat = lat
        self.last_home_lon = lon

        if not self.sitl.start(lat, lon):
            self.start_btn.config(state="normal")
            self.config(cursor="")
            self.map_widget.config(cursor="")
            self.right_panel.config(cursor="")
            return

        self.after(0, lambda: self.status_var.set("SITL 부팅 중... (약 20초)"))
        time.sleep(10)
        print("[INFO] Waiting SITL boot...")
        time.sleep(10)

        self.after(0, lambda: self.status_var.set("MAVLink 연결 및 하트비트 대기 중..."))

        try:
            self.drone = mavutil.mavlink_connection("tcp:127.0.0.1:5760")
            self.drone.wait_heartbeat(timeout=30)
            self.after(1000, lambda: self.status_var.set("드론 연결 성공! 홈 설정 중..."))
            self.add_practice_log("MAVLINK_CONNECTED", "HEARTBEAT", "CONNECTED", "tcp:127.0.0.1:5760")
            self.after(0, self.update_beginner_guide)
            self.update_command_explain(
                "MAVLink 연결 성공",
                "HEARTBEAT",
                "- SITL 실행\n- TCP 5760 연결\n- HEARTBEAT 수신",
                "MAVLink 연결이 완료되었습니다."
            )
            print("[MAVLink] Connected")
            self.config(cursor="")
            self.map_widget.config(cursor="")
            self.right_panel.config(cursor="")
            self.update_idletasks()
        except Exception as e:
            self.after(0, lambda: self.status_var.set(f"연결 실패: {e}"))
            self.start_btn.config(state="normal")
            self.config(cursor="")
            self.map_widget.config(cursor="")
            self.right_panel.config(cursor="")
            self.update_idletasks()
            return

        time.sleep(2)

        self.after(10, lambda: self.status_var.set("지도 위치 이동 및 마커 생성 중..."))
        self.map_widget.set_position(lat, lon)

        try:
            if self.home_icon:
                self.home_marker = self.map_widget.set_marker(lat, lon, text="HOME", icon=self.home_icon)
            else:
                self.home_marker = self.map_widget.set_marker(lat, lon, text="HOME")
        except Exception as e:
            print("[IMG ERROR]", e)
            self.home_marker = self.map_widget.set_marker(lat, lon, text="HOME")

        with self.mav_lock:
            # 자세 데이터 20Hz
            self.drone.mav.request_data_stream_send(  # type: ignore[union-attr]
                self.drone.target_system,  # type: ignore[union-attr]
                self.drone.target_component,  # type: ignore[union-attr]
                mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,
                20,
                1
            )

            # 지상속도 10Hz
            self.drone.mav.request_data_stream_send(  # type: ignore[union-attr]
                self.drone.target_system,  # type: ignore[union-attr]
                self.drone.target_component,  # type: ignore[union-attr]
                mavutil.mavlink.MAV_DATA_STREAM_EXTRA2,
                10,
                1
            )

            # 위치 데이터 10Hz
            self.drone.mav.request_data_stream_send(  # type: ignore[union-attr]
                self.drone.target_system,  # type: ignore[union-attr]
                self.drone.target_component,  # type: ignore[union-attr]
                mavutil.mavlink.MAV_DATA_STREAM_POSITION,
                10,
                1
            )

            # 상태 데이터 2Hz
            self.drone.mav.request_data_stream_send(  # type: ignore[union-attr]
                self.drone.target_system,  # type: ignore[union-attr]
                self.drone.target_component,  # type: ignore[union-attr]
                mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS,
                2,
                1
            )

        self.gps_enable_time = None
        self.after(0, lambda: self.status_var.set("모든 시스템 준비 완료 (GPS 활성화 대기)"))
        self.after(0, lambda: self.start_btn.config(text="Exit SITL", bg="#e74c3c", fg="white", state="normal"))

    def stop_sitl_action(self):
        if self.drone:
            try:
                self.drone.close()
            except:
                pass
            self.drone = None

        self.sitl.stop()

        if self.drone_marker:
            self.drone_marker.delete()
            self.drone_marker = None

        if self.home_marker:
            self.home_marker.delete()
            self.home_marker = None

        if self.target_marker:
            self.target_marker.delete()
            self.target_marker = None

        if self.path_line:
            self.path_line.delete()
            self.path_line = None

        try:
            self.hide_waypoint_panel()
        except Exception:
            pass

        self.init_msg_sent = False
        self.gps_enable_time = None
        self.joystick_visible.set(False)
        if hasattr(self, "js_panel_frame"):
            self.js_panel_frame.place_forget()
        self.reset_telemetry()
        self.hud.update_hud(0, 0, 0, 0, "DISCONN", 0, 0.0, 0, False)
        self.arm_status_label.config(text="상태: 연결 대기중", fg="#ecf0f1", font=("맑은 고딕", 11, "bold"))
        self.start_btn.config(text="Start SITL", bg="yellow", fg="black", state="normal")
        self.config(cursor="")
        self.map_widget.config(cursor="")
        self.right_panel.config(cursor="")
        self.update_idletasks()
        self.add_practice_log("EXIT_SITL", "SYSTEM", "DONE", "SITL 종료 완료")
        self.update_beginner_guide()
        self.save_practice_report()
        self.status_var.set("SITL 종료 완료. 실습 리포트 CSV가 저장되었습니다.")

    # -----------------------------------------------------------
    # MAVLink 수신 루프
    # -----------------------------------------------------------
    def mavlink_loop(self):
        while self.running:
            if not self.drone:
                time.sleep(0.01)
                continue

            if self.drone:
                try:
                    with self.mav_lock:
                        msg = self.drone.recv_match(blocking=False)

                    if not msg:
                        time.sleep(0.005)
                        continue

                    if self.gps_enable_time is None:
                        self.gps_enable_time = time.time() + 10
                        self.after(
                            0,
                            lambda: CountdownProgressBarPopup(self, 10, self.gps_enable_time, "GPS 초기화...")
                        )

                    msg_type = msg.get_type()

                    if msg_type == "GLOBAL_POSITION_INT":
                        lat = msg.lat / 1e7
                        lon = msg.lon / 1e7
                        self.d_lat = lat
                        self.d_lon = lon
                        self.d_alt = msg.relative_alt / 1000.0

                    elif msg_type == "ATTITUDE":
                        self.d_roll = math.degrees(msg.roll)
                        self.d_pitch = math.degrees(msg.pitch)
                        self.d_yaw = math.degrees(msg.yaw)

                    elif msg_type == "VFR_HUD":
                        self.d_speed = msg.groundspeed

                    elif msg_type == "GPS_RAW_INT":
                        self.d_sats = msg.satellites_visible

                    elif msg_type == "SYS_STATUS":
                        self.d_bat_v = msg.voltage_battery / 1000.0
                        self.d_bat_p = msg.battery_remaining
                        try:
                            if msg.onboard_control_sensors_health & msg.onboard_control_sensors_enabled == msg.onboard_control_sensors_enabled:
                                self.is_ready_to_arm = True
                            else:
                                self.is_ready_to_arm = False
                        except Exception:
                            pass

                    elif msg_type == "HEARTBEAT":
                        new_mode = mavutil.mode_string_v10(msg)
                        if self.last_mode is not None and self.last_mode != new_mode:
                            print(f"[알림] 비행 모드가 {self.last_mode} -> {new_mode}(으)로 변경되었습니다.")
                            self.status_var.set(f"모드 변경 완료: {new_mode}")
                        self.last_mode = new_mode
                        self.d_mode = new_mode
                        self.d_is_armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

                    elif msg_type == "STATUSTEXT":
                        text = msg.text
                        if "Arming checks passed" in text:
                            self.is_ready_to_arm = True
                            self.status_var.set("시동 준비 완료 (PreArm Passed)")

                except Exception as e:
                    print("[MAVLINK ERROR]", e)

            time.sleep(0.001)

    # -----------------------------------------------------------
    # GUI 업데이트 루프
    # -----------------------------------------------------------
    def update_drone_data(self):
        if not self.running or not self.winfo_exists():
            return

        try:
            current_zoom = round(self.map_widget.zoom)
            self.zoom_label.config(text=f"Zoom: {current_zoom}")
            self.zoom_label.lift()
        except Exception:
            pass

        self.hud.update_hud(
            self.d_roll,
            self.d_pitch,
            self.d_alt,
            self.d_speed,
            self.d_mode,
            self.d_sats,
            self.d_bat_v,
            self.d_bat_p,
            self.d_is_armed
        )

        self.update_beginner_guide()

        lat = self.d_lat
        lon = self.d_lon

        armable_modes = ["STABILIZE", "LOITER", "GUIDED", "ALT_HOLD", "POSHOLD"]
        current_mode = self.d_mode.strip().upper()

        if not self.drone:
            arm_text = "상태: 연결 대기중"
            arm_color = "#ecf0f1"
        elif self.d_is_armed:
            arm_text = "상태: ARMED (비행 중)"
            arm_color = "#2ecc71"
        else:
            if current_mode not in armable_modes:
                arm_text = f"상태: 시동 불가 ({current_mode})"
                arm_color = "#95a5a6"
            elif not self.is_ready_to_arm:
                arm_text = "상태: PreArm 점검 중..."
                arm_color = "#e67e22"
            else:
                arm_text = "상태: READY (시동 가능)"
                arm_color = "#3498db"

        self.arm_status_label.config(text=arm_text, fg=arm_color, font=("맑은 고딕", 11, "bold"))

        # 조이스틱 안전 조건 실시간 감시
        if self.joystick_visible.get():
            current_m = self.d_mode.strip().upper()
            if not self.drone or not self.d_is_armed or current_m not in ["LOITER", "ALT_HOLD"]:
                self.joystick_visible.set(False)
                self.toggle_joystick_view()
                self.status_var.set("비행 상태가 충족되지 않아 조이스틱 조종이 자동 차단되었습니다.")

        if self.gps_enable_time is None or time.time() < self.gps_enable_time:
            self.after_id = self.after(100, self.update_drone_data)
            return

        if lat == 0 and lon == 0:
            self.after_id = self.after(100, self.update_drone_data)
            return

        if not self.init_msg_sent:
            self.init_msg_sent = True
            self.after(5000, lambda: self.status_var.set("실시간 데이터 수신 중"))

        yaw_diff = abs(self.d_yaw - self.last_drone_yaw)

        if self.drone_marker is None:
            if self.drone_raw_img:
                rotated_pil = self.drone_raw_img.rotate(-self.d_yaw, resample=RESAMPLE_BICUBIC)
                self.drone_rotated_icon = ImageTk.PhotoImage(rotated_pil)
                self.drone_marker = self.map_widget.set_marker(self.d_lat, self.d_lon, icon=self.drone_rotated_icon)
            else:
                self.drone_marker = self.map_widget.set_marker(self.d_lat, self.d_lon, text="Drone")
            self.last_drone_yaw = self.d_yaw

        else:
            self.drone_marker.set_position(self.d_lat, self.d_lon)
            if yaw_diff > 1.0:
                if self.drone_raw_img:
                    rotated_pil = self.drone_raw_img.rotate(-self.d_yaw, resample=RESAMPLE_BICUBIC)
                    self.drone_rotated_icon = ImageTk.PhotoImage(rotated_pil)
                    try:
                        self.drone_marker.delete()
                    except:
                        pass
                    self.drone_marker = self.map_widget.set_marker(self.d_lat, self.d_lon, icon=self.drone_rotated_icon)
                    self.last_drone_yaw = self.d_yaw

        if self.target_marker:
            t_pos = self.target_marker.position
            if self.path_line:
                try:
                    self.path_line.set_position_list([(self.d_lat, self.d_lon), t_pos])
                except Exception:
                    try:
                        self.path_line.delete()
                    except:
                        pass
                    self.path_line = self.map_widget.set_path([(self.d_lat, self.d_lon), t_pos], color="red", width=2)
            else:
                self.path_line = self.map_widget.set_path([(self.d_lat, self.d_lon), t_pos], color="red", width=2)

        elif self.path_line:
            self.path_line.delete()
            self.path_line = None

        # 실제 실행 중인 경로선은 사용자가 지도를 다시 클릭해도 유지한다.
        # 빨간 선: 현재 선택/미리보기 TARGET
        # 민트색 굵은 선: 실제 MAVLink 이동 명령이 전송된 ACTIVE ROUTE
        if self.active_route_target:
            a_lat, a_lon, a_alt = self.active_route_target

            if self.active_route_line:
                try:
                    self.active_route_line.set_position_list([(self.d_lat, self.d_lon), (a_lat, a_lon)])
                except Exception:
                    try:
                        self.active_route_line.delete()
                    except:
                        pass
                    self.active_route_line = self.map_widget.set_path(
                        [(self.d_lat, self.d_lon), (a_lat, a_lon)],
                        color="#00ffcc",
                        width=4
                    )
            else:
                self.active_route_line = self.map_widget.set_path(
                    [(self.d_lat, self.d_lon), (a_lat, a_lon)],
                    color="#00ffcc",
                    width=4
                )

            try:
                remain_m = self.calc_distance_m(self.d_lat, self.d_lon, a_lat, a_lon)
                if remain_m <= 5.0:
                    self.status_var.set(f"ACTIVE ROUTE 목표 근처 도달: 남은 거리 {remain_m:.1f}m")
            except Exception:
                pass

        # 드론 고정 체크박스 활성화 시 항상 지도 중앙에 드론 매핑
        if self.map_center_mode.get() == 2:
            now = time.time()
            if now - self.last_map_center_update_time > 10:
                self.map_widget.set_position(self.d_lat, self.d_lon)
                self.last_map_center_update_time = now

        # 착륙 후 시동이 꺼지면 2초 뒤 타겟 마커 및 이동선 자동 제거
        if not self.d_is_armed:
            if self.target_marker or self.path_line:
                if self.disarm_time is None:
                    self.disarm_time = time.time()
                elif time.time() - self.disarm_time > 2.0:
                    self.clear_target_marker_and_path()
                    self.disarm_time = None
                    self.status_var.set("착륙 후 시동 해제가 감지되어 목적지 마커와 이동선은 정리했습니다.")
                    ToastMessage(self, "착륙 후 시동 해제가 감지되어 목적지 마커와 이동선을 정리했습니다.", duration=4000)
        else:
            self.disarm_time = None

        self.after_id = self.after(50, self.update_drone_data)

    # -----------------------------------------------------------
    # 종료 처리
    # -----------------------------------------------------------
    def cleanup(self):
        """atexit용 정리 함수. GUI가 이미 닫히는 중이면 중복 정리를 하지 않는다."""
        print("[EXIT] cleanup SITL")

        try:
            if hasattr(self, "stop_event"):
                self.stop_event.set()

            if (
                hasattr(self, "practice_logs")
                and self.practice_logs
                and not getattr(self, "report_saved", False)
            ):
                self.add_practice_log(
                    "APP_CLEANUP",
                    "SYSTEM",
                    "AUTO_SAVE",
                    "프로그램 종료 정리 중 자동 저장"
                )
                self.save_practice_report()

            if hasattr(self, "drone") and self.drone:
                try:
                    self.drone.close()
                except Exception:
                    pass
                self.drone = None

            if hasattr(self, "sitl"):
                self.sitl.stop()

        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")

    def cancel_all_after_events(self):
        """Tkinter after 예약 작업을 전부 취소한다."""
        try:
            after_ids = self.tk.call("after", "info")
            for after_id in after_ids:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
        except Exception:
            pass

    def on_close(self):
        """
        X 버튼 종료 처리.

        기존 문제 원인:
        - update_drone_data(), js_loop(), 지도 위젯 내부 콜백이 after로 계속 예약됨
        - 창 닫기 중 save_report/set_status가 다시 after를 만들 수 있음
        - running이 False가 된 뒤 다시 X를 누르면 return되어 destroy가 실행되지 않을 수 있음

        해결:
        - is_closing 플래그로 중복 종료 차단
        - running=False / stop_event.set()을 가장 먼저 처리
        - 리포트 저장 후 모든 after 콜백 취소
        - MAVLink, SITL, map_widget 순서로 정리
        - 마지막에 quit() + destroy()를 모두 호출
        """
        if getattr(self, "is_closing", False):
            return

        self.is_closing = True
        self.running = False

        try:
            self.stop_event.set()
        except Exception:
            pass

        # 종료 로그와 CSV 저장
        try:
            if hasattr(self, "practice_logs"):
                self.add_practice_log("APP_CLOSE", "SYSTEM", "REQUESTED", "창 닫기 이벤트")
                self.save_practice_report()
        except Exception as e:
            print(f"[REPORT CLOSE SAVE ERROR] {e}")

        # 예약된 after 콜백 중지
        try:
            if hasattr(self, "after_id") and self.after_id:
                try:
                    self.after_cancel(self.after_id)
                except Exception:
                    pass
                self.after_id = None

            self.cancel_all_after_events()
        except Exception:
            pass

        # 팝업 정리
        try:
            if hasattr(self, "countdown_popup") and self.countdown_popup:
                try:
                    self.countdown_popup.destroy()
                except Exception:
                    pass
                self.countdown_popup = None
        except Exception:
            pass

        # MAVLink 연결 해제
        try:
            with self.mav_lock:
                old_drone = self.drone
                self.drone = None

            if old_drone:
                try:
                    old_drone.close()
                except Exception:
                    pass
        except Exception:
            pass

        # SITL 종료
        try:
            self.sitl.stop()
        except Exception as e:
            print(f"[SITL CLOSE ERROR] {e}")

        # tkintermapview 내부 after/canvas 콜백 방지를 위해 지도 위젯 먼저 제거
        try:
            if hasattr(self, "map_widget") and self.map_widget.winfo_exists():
                self.map_widget.destroy()
        except Exception:
            pass

        # 메인루프 종료 + 창 제거
        try:
            self.quit()
        except Exception:
            pass

        try:
            self.destroy()
        except Exception as e:
            print(f"[DESTROY ERROR] {e}")

    def handle_crash(self, exc_type, exc_value, exc_traceback):
        print("[FATAL ERROR]")
        traceback.print_exception(exc_type, exc_value, exc_traceback)

        try:
            self.cleanup()
        except Exception:
            pass

        sys.exit(1)

# ===============================================================
# 8. 카운트다운 팝업 클래스
# ===============================================================
class CountdownProgressBarPopup(tk.Toplevel):
    def __init__(self, parent, seconds, target_time, title_text="대기 중..."):
        super().__init__(parent)

        self.parent = parent
        self.parent.countdown_popup = self
        self.target_time = target_time
        self.title(title_text)

        width, height = 300, 60
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()

        pos_x = parent_x + (parent.winfo_width() // 2) - (width // 2)
        pos_y = parent_y + (parent.winfo_height() // 2) - (height * 4)

        self.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self.progress = ttk.Progressbar(
            self,
            orient="horizontal",
            length=250,
            mode="determinate"
        )
        self.progress.pack(pady=(10, 2))
        self.progress["maximum"] = seconds

        self.time_label = tk.Label(
            self,
            text=f"{seconds}초",
            font=("맑은 고딕", 10, "bold")
        )
        self.time_label.pack(pady=(0, 5))

        self.update_countdown()

    def update_countdown(self):
        if not self.parent.running or not self.parent.drone:
            try:
                self.parent.countdown_popup = None
                self.destroy()
            except:
                pass
            return

        now = time.time()
        remaining_float = self.target_time - now
        remaining_int = int(max(0, remaining_float))

        if remaining_float > 0:
            self.progress["value"] = self.progress["maximum"] - remaining_float
            self.time_label.config(text=f"{remaining_int}초 후 오픈", fg="blue")

            if remaining_float < 10:
                self.parent.status_var.set(f"시스템 안정화 대기 중... ({remaining_int}초 남음)")

            self.after(100, self.update_countdown)

        else:
            self.parent.status_var.set("준비 완료!!")
            self.parent.countdown_popup = None
            self.destroy()


# ===============================================================
# 9. 실행부
# ===============================================================
if __name__ == "__main__":
    app = GCSMap()
    app.mainloop()
