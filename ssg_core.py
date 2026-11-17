# ssg_core.py
"""YM2149 / AY-3-8910 (SSG) 核心封装。

移植自 OPMMIDI-GAI2(SSG)。emu2149 内部按 PSG_CLOCK 1789772 Hz 分频，
直接输出 55930 Hz —— 与 OPM 同采样率，混音时不需要重采样。

单通道满幅 0xFF<<4 = 4080，三通道合计约 12240（int16 的 37%）。
"""
from __future__ import annotations

import ctypes
import os

import numpy as np

PSG_CLOCK = 1_789_772          # MSX / PC-88 标准
OUTPUT_RATE = 55_930           # 与 OPM 时钟 / 64 对齐

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DLL = os.path.join(HERE, "ssg.dll")


class SsgCore:
    name = "emu2149"

    def __init__(self, dll_path: str = DEFAULT_DLL):
        self.dll = ctypes.CDLL(dll_path)
        self.dll.ssg_create.restype = ctypes.c_void_p
        self.dll.ssg_create.argtypes = []
        self.dll.ssg_write.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8]
        self.dll.ssg_generate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                                          ctypes.c_int]
        self.dll.ssg_destroy.argtypes = [ctypes.c_void_p]

        self.handle = self.dll.ssg_create()
        if not self.handle:
            raise RuntimeError("ssg_create 失败")
        self.write(0x07, 0x3F)      # 三通道全开

    def write(self, reg: int, val: int) -> None:
        self.dll.ssg_write(self.handle, reg & 0xFF, val & 0xFF)

    def generate_into(self, nframes: int, buf) -> None:
        """写进调用方预分配的 ctypes int16 缓冲区（单声道）。"""
        self.dll.ssg_generate(self.handle, buf, nframes)

    def generate(self, nframes: int) -> np.ndarray:
        buf = (ctypes.c_int16 * nframes)()
        self.generate_into(nframes, buf)
        return np.frombuffer(buf, dtype="<i2", count=nframes).astype(np.int16)

    def close(self) -> None:
        if self.handle:
            self.dll.ssg_destroy(self.handle)
            self.handle = None
