"""
voice_relay.py — серверная часть голосового чата (только стандартная библиотека).

Голос идёт по тому же зашифрованному каналу, что и текст, но отдельным бинарным
кадром (JSON-сообщения всегда начинаются с «{», поэтому путаницы нет):

    клиент -> сервер : VOICE_TAG + <аудио>
    сервер -> клиент : VOICE_TAG + <длина имени, 1 байт> + <имя, utf-8> + <аудио>

Голосовая комната привязана к чату (общий, группа, личный диалог). Человек может
сидеть только в одной комнате. Сервер ничего не декодирует, а просто пересылает
кадр остальным участникам комнаты, поэтому ему не нужны ни numpy, ни звуковые
библиотеки.
"""

from __future__ import annotations

import queue
import threading
import time

VOICE_TAG = b"\x01"
SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000        # 320 отсчётов на кадр

MAX_PAYLOAD = 1024
OUTBOX_FRAMES = 40                                    # ~0,8 с очереди на получателя
RATE_BURST, RATE_PER_SEC = 100, 70                    # антифлуд: нормальный поток — 50 кадров/с

Change = tuple  # (chat_id, [участники], audience | None)


def pack_voice(name: str, payload: bytes) -> bytes:
    raw = name.encode("utf-8")[:255]
    return VOICE_TAG + bytes([len(raw)]) + raw + payload


def split_voice(data: bytes) -> tuple[str, bytes] | None:
    """Разбирает кадр сервер -> клиент. None, если кадр повреждён."""
    if len(data) < 3 or data[:1] != VOICE_TAG:
        return None
    n = data[1]
    if len(data) < 3 + n:
        return None
    try:
        name = data[2:2 + n].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return name, data[2 + n:]


class _Outbox:
    """Своя очередь и поток отправки на каждого слушателя.

    Медленный получатель не должен тормозить говорящего и остальных: если очередь
    переполнилась, выбрасываются самые старые кадры (для голоса это лучше задержки).
    """

    def __init__(self, chan) -> None:
        self._chan = chan
        self._q: queue.Queue = queue.Queue(OUTBOX_FRAMES)
        threading.Thread(target=self._run, daemon=True).start()

    def put(self, packet: bytes) -> None:
        while True:
            try:
                self._q.put_nowait(packet)
                return
            except queue.Full:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass

    def close(self) -> None:
        self.put(None)

    def _run(self) -> None:
        while True:
            packet = self._q.get()
            if packet is None:
                return
            try:
                self._chan.send(packet)
            except Exception:
                return          # канал закрыт — клиента снимет его собственный поток


class _Room:
    def __init__(self, audience) -> None:
        self.members: dict[str, _Outbox] = {}
        self.audience = audience                      # None — комната видна всем


class VoiceRooms:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rooms: dict[str, _Room] = {}
        self._where: dict[str, str] = {}
        self._buckets: dict[str, list[float]] = {}

    def join(self, name: str, chan, chat_id: str, audience) -> list[Change]:
        """Сажает пользователя в комнату. Возвращает изменившиеся комнаты для рассылки."""
        with self._lock:
            changes = self._leave_locked(name)
            room = self._rooms.get(chat_id)
            if room is None:
                room = self._rooms[chat_id] = _Room(audience)
            room.members[name] = _Outbox(chan)
            self._where[name] = chat_id
            self._buckets[name] = [float(RATE_BURST), time.monotonic()]
            changes.append((chat_id, sorted(room.members), room.audience))
        return changes

    def leave(self, name: str) -> list[Change]:
        with self._lock:
            return self._leave_locked(name)

    def _leave_locked(self, name: str) -> list[Change]:
        chat_id = self._where.pop(name, None)
        self._buckets.pop(name, None)
        room = self._rooms.get(chat_id) if chat_id else None
        if room is None:
            return []
        box = room.members.pop(name, None)
        if box is not None:
            box.close()
        members = sorted(room.members)
        if not room.members:
            del self._rooms[chat_id]
        return [(chat_id, members, room.audience)]

    def relay(self, name: str, data: bytes) -> None:
        """Кадр от name -> всем остальным в его комнате."""
        payload = data[1:]
        if not payload or len(payload) > MAX_PAYLOAD:
            return
        now = time.monotonic()
        with self._lock:
            chat_id = self._where.get(name)
            if chat_id is None:
                return
            bucket = self._buckets[name]
            bucket[0] = min(float(RATE_BURST), bucket[0] + (now - bucket[1]) * RATE_PER_SEC)
            bucket[1] = now
            if bucket[0] < 1.0:
                return
            bucket[0] -= 1.0
            boxes = [b for n, b in self._rooms[chat_id].members.items() if n != name]
        if boxes:
            packet = pack_voice(name, payload)
            for box in boxes:
                box.put(packet)

    def snapshot_for(self, name: str) -> dict[str, list[str]]:
        """Комнаты, которые видит пользователь, и кто в них сидит."""
        with self._lock:
            return {cid: sorted(room.members) for cid, room in self._rooms.items()
                    if room.audience is None or name in room.audience}

    def room_of(self, name: str) -> str | None:
        with self._lock:
            return self._where.get(name)
