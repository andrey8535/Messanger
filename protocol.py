"""
protocol.py — wire framing and the encrypted channel.

Two independent layers live here:

  1. Framing       how a raw TCP byte stream is cut into discrete messages.
                   Every frame on the wire is:
                       [ 4-byte big-endian length | payload ]

  2. SecureChannel an authenticated, encrypted tunnel that rides on top of
                   that framing:
                       handshake  = X25519 ECDH -> HKDF-SHA256 -> 2 AES keys
                       per message = AES-256-GCM with a counter nonce

Nothing here knows what a "chat message" is. That is the job of the
application layer in server.py / client.py, which just calls
SecureChannel.send(bytes) / .recv() -> bytes and puts JSON inside.
"""

from __future__ import annotations

import socket
import struct
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

__all__ = [
    "send_frame",
    "recv_frame",
    "recv_exactly",
    "SecureChannel",
    "SocketTransport",
    "WSTransport",
    "as_transport",
    "handshake",
    "InvalidTag",
]

# --------------------------------------------------------------------------- #
# Layer 1: length-prefixed framing over a raw TCP stream                      #
# --------------------------------------------------------------------------- #
#
# TCP is a *stream*: recv() may hand you half a message or three messages
# glued together. To get discrete messages back we prefix every payload with
# its length, then read exactly that many bytes.

_LEN = struct.Struct(">I")   # 4 bytes, unsigned, big-endian
MAX_FRAME = 1 << 20          # 1 MiB cap so a bogus length can't blow up memory


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``sock`` or raise ConnectionError on EOF."""
    chunks: list[bytes] = []
    remaining = n
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("peer closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock: socket.socket, payload: bytes) -> None:
    """Send one length-prefixed frame."""
    if len(payload) > MAX_FRAME:
        raise ValueError("frame too large")
    sock.sendall(_LEN.pack(len(payload)) + payload)


def recv_frame(sock: socket.socket) -> bytes:
    """Receive one length-prefixed frame."""
    (length,) = _LEN.unpack(recv_exactly(sock, _LEN.size))
    if length > MAX_FRAME:
        raise ValueError(f"declared frame size {length} exceeds cap")
    return recv_exactly(sock, length)


# --------------------------------------------------------------------------- #
# Layer 2: the encrypted channel                                             #
# --------------------------------------------------------------------------- #

_HKDF_INFO = b"python-chat-demo/v1 aead keys"


class SocketTransport:
    """Транспорт поверх TCP-сокета: кадры с префиксом длины."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def send(self, data: bytes) -> None:
        send_frame(self._sock, data)

    def recv(self) -> bytes:
        return recv_frame(self._sock)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class WSTransport:
    """Транспорт поверх WebSocket (websockets.sync): одно бинарное сообщение = один кадр.

    Подходит и серверному, и клиентскому соединению. Библиотека websockets здесь не
    импортируется, поэтому TCP-режим не требует её установки.
    """

    def __init__(self, ws) -> None:
        self._ws = ws

    def send(self, data: bytes) -> None:
        try:
            self._ws.send(data)
        except Exception as exc:
            if type(exc).__name__.startswith("ConnectionClosed"):
                raise ConnectionError("peer closed the connection") from exc
            raise

    def recv(self) -> bytes:
        try:
            msg = self._ws.recv()
        except Exception as exc:
            if type(exc).__name__.startswith("ConnectionClosed"):
                raise ConnectionError("peer closed the connection") from exc
            raise
        if isinstance(msg, str):
            raise ValueError("unexpected text frame")
        return msg

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


def as_transport(conn):
    """Сокет -> SocketTransport; готовый транспорт возвращается как есть."""
    return SocketTransport(conn) if isinstance(conn, socket.socket) else conn


class SecureChannel:
    """
    An encrypted, authenticated, ordered message channel over one transport
    (a TCP socket or a WebSocket).

      * Confidentiality + integrity : AES-256-GCM (an AEAD cipher). A single
        wrong bit anywhere in a frame makes decryption raise InvalidTag — you
        cannot silently tamper with the ciphertext.
      * Key separation              : a different AES key for each direction,
        so client->server and server->client never share a keystream.
      * Replay / reorder protection : the nonce is a strict per-direction
        message counter; recv() rejects any frame whose nonce is not the next
        one expected.

    Thread-safety: send() may be called from several threads at once (the
    server broadcasts to one client from many sender threads); recv() is
    single-reader.
    """

    NONCE_LEN = 12  # 96-bit nonce, the standard size for AES-GCM

    def __init__(self, conn, send_key: bytes, recv_key: bytes) -> None:
        self._t = as_transport(conn)
        self._enc = AESGCM(send_key)
        self._dec = AESGCM(recv_key)
        self._send_ctr = 0
        self._recv_ctr = 0
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()

    @classmethod
    def _nonce(cls, counter: int) -> bytes:
        # 12-byte big-endian counter. Unique per (key, direction) because the
        # key is ephemeral (new X25519 keypair every connection), so a simple
        # counter is a safe nonce and can never repeat within a connection.
        return counter.to_bytes(cls.NONCE_LEN, "big")

    def send(self, plaintext: bytes) -> None:
        with self._send_lock:
            nonce = self._nonce(self._send_ctr)
            self._send_ctr += 1
            ciphertext = self._enc.encrypt(nonce, plaintext, None)
            # The nonce is sent in the clear alongside the ciphertext; that is
            # fine and expected for GCM. What must never leak is the key.
            self._t.send(nonce + ciphertext)

    def recv(self) -> bytes:
        with self._recv_lock:
            frame = self._t.recv()
            nonce, ciphertext = frame[: self.NONCE_LEN], frame[self.NONCE_LEN:]
            if nonce != self._nonce(self._recv_ctr):
                raise ValueError("unexpected nonce — replayed, dropped or reordered frame")
            plaintext = self._dec.decrypt(nonce, ciphertext, None)  # raises InvalidTag if forged
            self._recv_ctr += 1
            return plaintext

    def close(self) -> None:
        self._t.close()


# --------------------------------------------------------------------------- #
# Handshake: turn a fresh connection into a SecureChannel                     #
# --------------------------------------------------------------------------- #

def handshake(conn, *, server: bool) -> SecureChannel:
    """
    Ephemeral X25519 Diffie-Hellman key agreement.

        1. each side generates a throwaway keypair
        2. the 32-byte public keys are swapped in the clear (2 frames)
        3. each side computes  shared = X25519(my_private, their_public);
           the shared secret is identical on both ends and never touches
           the wire
        4. HKDF-SHA256 stretches that secret into 64 bytes = two AES-256
           keys, one for each direction

    The private keys are discarded when this function returns, so a recording
    of the traffic cannot be decrypted later even if a machine is fully
    compromised afterwards (forward secrecy).

    NOTE: this is an *unauthenticated* exchange — good enough to study how
    encrypted transport works, but on a hostile network it is open to a
    man-in-the-middle. Real systems (TLS, the Signal protocol) bolt an
    identity check onto this same core.
    """
    transport = as_transport(conn)
    my_priv = X25519PrivateKey.generate()
    my_pub = my_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    transport.send(my_pub)
    peer_pub_raw = transport.recv()
    if len(peer_pub_raw) != 32:
        raise ValueError("bad peer public key length")

    shared = my_priv.exchange(X25519PublicKey.from_public_bytes(peer_pub_raw))

    # Bind the derived keys to this specific pair of public keys, ordered the
    # same way on both ends (client key first) so the salt matches.
    client_pub, server_pub = (
        (peer_pub_raw, my_pub) if server else (my_pub, peer_pub_raw)
    )
    material = HKDF(
        algorithm=SHA256(),
        length=64,
        salt=client_pub + server_pub,
        info=_HKDF_INFO,
    ).derive(shared)

    c2s_key, s2c_key = material[:32], material[32:]
    if server:
        return SecureChannel(transport, send_key=s2c_key, recv_key=c2s_key)
    return SecureChannel(transport, send_key=c2s_key, recv_key=s2c_key)
