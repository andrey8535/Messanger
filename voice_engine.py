"""
voice_engine.py — клиентская часть голосового чата: микрофон -> сжатие -> сеть
и сеть -> микширование -> динамики. Нужны numpy и sounddevice.

Формат: 16 кБ/с моно, 16 бит, кадры по 20 мс. Перед отправкой кадр сжимается
логарифмическим 8-битным кодеком (как µ-law): 320 байт на кадр, ~128 кбит/с, пока
человек говорит. Тишина не передаётся (простой порог громкости + «хвост» 0,4 с).
Эхоподавления нет, поэтому лучше пользоваться наушниками.
"""

from __future__ import annotations

import collections
import math
import queue
import threading
import time

from voice_relay import FRAME_SAMPLES, SAMPLE_RATE, VOICE_TAG

try:
    import numpy as np
    import sounddevice as sd
    AVAILABLE, IMPORT_ERROR = True, ""
except Exception as exc:                      # нет пакетов — клиент просто отключит голос
    np = sd = None
    AVAILABLE, IMPORT_ERROR = False, str(exc)

INSTALL_HINT = "pip install sounddevice numpy"

# Порог громкости (RMS по шкале int16) для пресетов чувствительности.
SENSITIVITY = {"high": 150.0, "mid": 350.0, "low": 800.0}

HANGOVER_FRAMES = 20          # сколько кадров слать после последнего громкого (0,4 с)
PREBUFFER_FRAMES = 3          # накопить перед началом воспроизведения (60 мс)
MAX_DEPTH_FRAMES = 12         # больше 240 мс не копим, иначе голос отстаёт от жизни
SPEAK_HOLD = 0.3              # сколько секунд после громкого кадра человек «говорит»
SPEAK_RMS = 120.0             # громкость кадра, с которой считаем, что человек говорит
_LOG256 = math.log(256.0)


class VoiceError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


# --------------------------------------------------------------------------- #
# Кодек                                                                       #
# --------------------------------------------------------------------------- #

def _build_decode_table():
    q = np.arange(256)
    mag = (q & 0x7F) / 127.0
    val = np.expm1(mag * _LOG256) / 255.0
    val = np.where(q & 0x80, -val, val)
    return np.clip(np.rint(val * 32767.0), -32768, 32767).astype(np.int16)


_DECODE = _build_decode_table() if AVAILABLE else None


def encode(samples) -> bytes:
    """int16-отсчёты -> байты (1 байт на отсчёт: знак + 7 бит логарифма модуля)."""
    x = samples.astype(np.float32) / 32768.0
    mag = np.log1p(255.0 * np.abs(x)) / _LOG256
    q = np.rint(mag * 127.0).astype(np.uint8)
    q |= (x < 0).astype(np.uint8) << 7
    return q.tobytes()


def decode(payload: bytes):
    return _DECODE[np.frombuffer(payload, dtype=np.uint8)]


def rms(samples) -> float:
    f = samples.astype(np.float32)
    return float(np.sqrt(np.mean(f * f)))


# --------------------------------------------------------------------------- #
# Устройства                                                                  #
# --------------------------------------------------------------------------- #

def list_devices() -> tuple[list[str], list[str]]:
    """Имена микрофонов и устройств вывода. Только основной host API: иначе в Windows
    каждое устройство повторяется по 3–4 раза (MME, DirectSound, WASAPI, WDM-KS)."""
    if not AVAILABLE:
        return [], []
    try:
        devices, api = sd.query_devices(), sd.default.hostapi
    except Exception:
        return [], []
    ins: list[str] = []
    outs: list[str] = []
    for d in devices:
        name = d["name"]
        if d["hostapi"] != api or "Sound Mapper" in name:
            continue
        if d["max_input_channels"] > 0 and name not in ins:
            ins.append(name)
        if d["max_output_channels"] > 0 and name not in outs:
            outs.append(name)
    return ins, outs


def refresh_devices() -> None:
    """Заставляет PortAudio заново опросить устройства (например, после подключения наушников)."""
    if AVAILABLE:
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass


def _find_device(name: str, kind: str):
    if not name:
        return None
    try:
        api = sd.default.hostapi
        for i, d in enumerate(sd.query_devices()):
            if d["hostapi"] == api and d["name"] == name and d[f"max_{kind}_channels"] > 0:
                return i
    except Exception:
        pass
    return None                                # устройство пропало — берём системное


def _explain(exc: Exception) -> str:
    return (f"Не удалось открыть микрофон или динамики: {exc}. Проверьте, что устройство подключено "
            f"и что Windows разрешает приложениям доступ к микрофону "
            f"(Параметры → Конфиденциальность → Микрофон).")


# --------------------------------------------------------------------------- #
# Движок                                                                      #
# --------------------------------------------------------------------------- #

class _Peer:
    """Входящий поток одного собеседника: очередь кадров (джиттер-буфер) и признак «говорит»."""

    def __init__(self) -> None:
        self.q: collections.deque = collections.deque()
        self.primed = False
        self.last_rx = 0.0
        self.loud_until = 0.0

    def push(self, frame, now: float) -> None:
        self.q.append(frame)
        self.last_rx = now
        if rms(frame) >= SPEAK_RMS:
            self.loud_until = now + SPEAK_HOLD
        while len(self.q) > MAX_DEPTH_FRAMES:
            self.q.popleft()

    def pull(self, n: int):
        if not self.primed:
            if len(self.q) < PREBUFFER_FRAMES:
                return None
            self.primed = True
        parts, need = [], n
        while need and self.q:
            head = self.q[0]
            if head.size <= need:
                parts.append(head)
                need -= head.size
                self.q.popleft()
            else:
                parts.append(head[:need])
                self.q[0] = head[need:]
                need = 0
        if need:
            self.primed = False                # буфер опустел — снова копим
        if not parts:
            return None
        out = np.concatenate(parts) if len(parts) > 1 else parts[0]
        if need:
            out = np.concatenate((out, np.zeros(need, dtype=np.int16)))
        return out.astype(np.int32)


class VoiceEngine:
    def __init__(self, send_packet) -> None:
        self._send = send_packet                # принимает bytes, потокобезопасен
        self._lock = threading.Lock()
        self._peers: dict[str, _Peer] = {}
        self._in_q: queue.Queue = queue.Queue(maxsize=50)
        self._in_stream = self._out_stream = self._thread = None
        self._stop = threading.Event()
        self._hang = 0
        self._last_loud = 0.0
        self.active = False
        self.muted = False
        self.threshold = SENSITIVITY["mid"]
        self.volume = 1.0

    # ---- запуск / остановка ------------------------------------------------ #

    def start(self, input_name: str = "", output_name: str = "") -> None:
        if not AVAILABLE:
            raise VoiceError(f"Для голосового чата нужны библиотеки: {INSTALL_HINT} ({IMPORT_ERROR})")
        self.stop()
        self._stop.clear()
        try:
            kw = dict(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES, latency="low")
            self._in_stream = sd.InputStream(device=_find_device(input_name, "input"),
                                             callback=self._on_input, **kw)
            self._out_stream = sd.OutputStream(device=_find_device(output_name, "output"),
                                               callback=self._on_output, **kw)
            self._in_stream.start()
            self._out_stream.start()
        except Exception as exc:
            self.stop()
            raise VoiceError(_explain(exc)) from exc
        self.active = True
        self._thread = threading.Thread(target=self._send_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.active = False
        self._stop.set()
        for attr in ("_in_stream", "_out_stream"):
            stream = getattr(self, attr)
            setattr(self, attr, None)
            if stream is not None:
                try:
                    stream.abort()
                    stream.close()
                except Exception:
                    pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            self._peers.clear()
        while True:
            try:
                self._in_q.get_nowait()
            except queue.Empty:
                break
        self._hang = 0

    # ---- микрофон -> сеть -------------------------------------------------- #

    def _on_input(self, indata, frames, time_info, status) -> None:
        try:
            self._in_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def gate(self, frame):
        """Решает, слать ли кадр. Возвращает готовый пакет или None (тишина / микрофон выключен)."""
        if self.muted:
            self._hang = 0
            return None
        level = rms(frame)
        if level >= self.threshold:
            self._hang = HANGOVER_FRAMES
            self._last_loud = time.monotonic()
        elif self._hang > 0:
            self._hang -= 1
        else:
            return None
        return VOICE_TAG + encode(frame)

    def _send_loop(self) -> None:
        pending = np.empty(0, dtype=np.int16)
        while not self._stop.is_set():
            try:
                chunk = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            pending = np.concatenate((pending, chunk)) if pending.size else chunk
            while pending.size >= FRAME_SAMPLES:
                frame, pending = pending[:FRAME_SAMPLES], pending[FRAME_SAMPLES:]
                packet = self.gate(frame)
                if packet is None:
                    continue
                try:
                    self._send(packet)
                except Exception:
                    self._stop.set()           # связь пропала; приложение само узнает и остановит голос
                    return

    # ---- сеть -> динамики -------------------------------------------------- #

    def feed(self, name: str, payload: bytes) -> None:
        """Принять кадр собеседника (вызывается из сетевого потока)."""
        if not self.active or not payload:
            return
        try:
            frame = decode(payload)
        except Exception:
            return
        now = time.monotonic()
        with self._lock:
            peer = self._peers.get(name)
            if peer is None:
                peer = self._peers[name] = _Peer()
            peer.push(frame, now)

    def retain(self, names) -> None:
        """Забыть тех, кто вышел из комнаты."""
        with self._lock:
            for name in [n for n in self._peers if n not in names]:
                del self._peers[name]

    def _on_output(self, outdata, frames, time_info, status) -> None:
        mix = np.zeros(frames, dtype=np.int32)
        with self._lock:
            for peer in self._peers.values():
                chunk = peer.pull(frames)
                if chunk is not None:
                    mix += chunk
        if self.volume != 1.0:
            mix = (mix * self.volume).astype(np.int32)
        np.clip(mix, -32768, 32767, out=mix)
        outdata[:, 0] = mix.astype(np.int16)

    # ---- индикаторы -------------------------------------------------------- #

    def speaking(self) -> set[str]:
        """Кто из собеседников говорит прямо сейчас."""
        now = time.monotonic()
        with self._lock:
            for name in [n for n, p in self._peers.items() if now - p.last_rx > 10.0 and not p.q]:
                del self._peers[name]
            return {n for n, p in self._peers.items() if now < p.loud_until}

    def transmitting(self) -> bool:
        return not self.muted and (time.monotonic() - self._last_loud) < SPEAK_HOLD
