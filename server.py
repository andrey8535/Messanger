# """
# server.py — a multi-client chat relay with encrypted links.
#
#     python server.py --host 127.0.0.1 --port 9001
#
# Each client gets its own thread. The link to every client is a SecureChannel
# (see protocol.py), so the bytes on the wire are AES-GCM ciphertext.
#
# Trust model: the server *does* see plaintext chat. It decrypts a message from
# the sender and re-encrypts it for each recipient. This is "encryption in
# transit" — the same model as a TLS-based chat server — not end-to-end
# encryption. A passive network sniffer learns nothing; the server operator
# sees everything.
# """
#
# from __future__ import annotations
#
# import argparse
# import json
# import socket
# import threading
# import time
#
# from protocol import InvalidTag, SecureChannel, handshake
#
# MAX_NAME_LEN = 32
# MAX_TEXT_LEN = 4000
#
#
# class Hub:
#     """Registry of connected clients plus broadcast."""
#
#     def __init__(self) -> None:
#         self._clients: dict[str, SecureChannel] = {}
#         self._lock = threading.Lock()
#
#     def add(self, name: str, chan: SecureChannel) -> None:
#         with self._lock:
#             self._clients[name] = chan
#
#     def remove(self, name: str) -> None:
#         with self._lock:
#             self._clients.pop(name, None)
#
#     def has(self, name: str) -> bool:
#         with self._lock:
#             return name in self._clients
#
#     def names(self) -> list[str]:
#         with self._lock:
#             return sorted(self._clients)
#
#     def send_to(self, name: str, payload: dict) -> None:
#         with self._lock:
#             chan = self._clients.get(name)
#         if chan is not None:
#             try:
#                 chan.send(json.dumps(payload).encode())
#             except OSError:
#                 pass
#
#     def broadcast(self, payload: dict, *, exclude: str | None = None) -> None:
#         raw = json.dumps(payload).encode()
#         with self._lock:
#             targets = [c for n, c in self._clients.items() if n != exclude]
#         for chan in targets:
#             try:
#                 chan.send(raw)
#             except OSError:
#                 pass  # the owning thread will clean this client up
#
#     def send_to_members(self, members: list[str], payload: dict, *, exclude: str | None = None) -> None:
#         raw = json.dumps(payload).encode()
#         with self._lock:
#             targets = [self._clients[m] for m in members if m in self._clients and m != exclude]
#         for chan in targets:
#             try:
#                 chan.send(raw)
#             except OSError:
#                 pass
#
#         def serve_client(conn: socket.socket, addr: tuple[str, int], hub: Hub, db: SupabaseDB | None = None) -> None:
#             peer = f"{addr[0]}:{addr[1]}"
#             name: str | None = None
#             chan: SecureChannel | None = None
#             if db is None:
#                 db = SupabaseDB()
#
#             try:
#                 # 1. establish the encrypted channel
#                 chan = handshake(conn, server=True)
#
#                 # 2. authentication phase (login, register, or legacy join)
#                 while True:
#                     raw_data = chan.recv()
#                     if not raw_data:
#                         return
#                     req = json.loads(raw_data)
#                     req_type = req.get("type")
#
#                     if req_type == "register":
#                         username = str(req.get("username", "")).strip()[:MAX_NAME_LEN]
#                         password = str(req.get("password", ""))
#
#                         if not db.is_configured():
#                             chan.send(json.dumps({
#                                 "type": "error",
#                                 "text": "Сервер: Supabase API Key не настроен в supabase_config.json"
#                             }).encode())
#                             continue
#
#                         ok, message = db.register_user(username, password)
#                         if ok:
#                             print(f"[*] Регистрация: '{username}' успешно зарегистрирован ({peer})")
#                             chan.send(json.dumps({
#                                 "type": "register_ok",
#                                 "text": f"Пользователь '{username}' успешно зарегистрирован! Теперь выполните вход."
#                             }).encode())
#                         else:
#                             chan.send(json.dumps({"type": "error", "text": message}).encode())
#                         continue
#
#                     elif req_type == "login":
#                         username = str(req.get("username", "")).strip()[:MAX_NAME_LEN]
#                         password = str(req.get("password", ""))
#
#                         if not username:
#                             chan.send(json.dumps({"type": "error", "text": "Введите имя пользователя"}).encode())
#                             continue
#
#                         if db.is_configured():
#                             ok, message = db.authenticate_user(username, password)
#                             if not ok:
#                                 chan.send(json.dumps({"type": "error", "text": message}).encode())
#                                 continue
#                         else:
#                             chan.send(json.dumps({
#                                 "type": "error",
#                                 "text": "База данных Supabase не настроена на сервере (укажите API Key в supabase_config.json)"
#                             }).encode())
#                             continue
#
#                         if hub.has(username):
#                             chan.send(
#                                 json.dumps({"type": "error", "text": f"Пользователь '{username}' уже в сети"}).encode())
#                             continue
#
#                         name = username
#                         break
#
#                     elif req_type == "join":
#                         # Обратная совместимость для гостевого режима
#                         username = str(req.get("name", "")).strip()[:MAX_NAME_LEN]
#                         if not username:
#                             chan.send(json.dumps({"type": "error", "text": "Имя не может быть пустым"}).encode())
#                             continue
#
#                         if db.is_configured():
#                             chan.send(json.dumps({
#                                 "type": "error",
#                                 "text": "На сервере включена база данных Supabase. Пожалуйста, выполните вход с паролем."
#                             }).encode())
#                             continue
#
#                         if hub.has(username):
#                             chan.send(json.dumps({"type": "error", "text": f"Имя '{username}' уже занято"}).encode())
#                             continue
#
#                         name = username
#                         break
#
#                     elif req_type == "quit":
#                         return
#                     else:
#                         chan.send(json.dumps(
#                             {"type": "error", "text": "Ожидается авторизация (вход или регистрация)"}).encode())
#                         continue
#
#                 # 3. Successful authentication
#                 hub.add(name, chan)
#                 print(f"[+] {name} вошел в чат с {peer} ({len(hub.names())} онлайн)")
#                 chan.send(json.dumps({"type": "welcome", "you": name, "online": hub.names()}).encode())
#
#                 # Отправляем список чатов пользователя (Общий, группы, личные)
#                 user_chats = [{"id": "general", "name": "Общий чат", "type": "global", "members": []}]
#                 if db and db.is_configured():
#                     try:
#                         user_chats = db.get_user_chats(name)
#                     except Exception as e:
#                         print(f"[!] Ошибка загрузки списка чатов для {name}: {e}")
#                 chan.send(json.dumps({"type": "chat_list", "chats": user_chats}).encode())
#                 chan.send(json.dumps({"type": "voice_snapshot", "rooms": hub.voice.snapshot_for(name)}).encode())
#
#                 # Отправляем последние сохраненные сообщения из общего чата
#                 if db and db.is_configured():
#                     try:
#                         recent_msgs = db.get_recent_messages(chat_id="general", limit=50)
#                         if recent_msgs:
#                             chan.send(
#                                 json.dumps({"type": "history", "chat_id": "general", "messages": recent_msgs}).encode())
#                     except Exception as e:
#                         print(f"[!] Ошибка отправки истории для {name}: {e}")
#
#                 hub.broadcast({"type": "system", "text": f"{name} присоединился к чату"}, exclude=name)
#
#                 # 4. relay loop
#                 while True:
#                     raw_msg = chan.recv()
#                     if not raw_msg:
#                         break
#                     if raw_msg[:1] == VOICE_TAG:
#                         hub.voice.relay(name, raw_msg)
#                         continue
#                     msg = json.loads(raw_msg)
#                     kind = msg.get("type")
#
#                     if kind == "voice_join":
#                         chat_id = str(msg.get("chat_id", "general"))
#                         audience = None
#                         if chat_id != "general":
#                             members = db.get_chat_members(chat_id) if db and db.is_configured() else []
#                             if name not in members:
#                                 chan.send(json.dumps({"type": "voice_error",
#                                                       "text": "Нет доступа к голосовому чату этого диалога."}).encode())
#                                 continue
#                             audience = members
#                         for change in hub.voice.join(name, chan, chat_id, audience):
#                             announce_voice(hub, *change)
#
#                     elif kind == "voice_leave":
#                         for change in hub.voice.leave(name):
#                             announce_voice(hub, *change)
#
#                     elif kind == "msg":
#                         chat_id = str(msg.get("chat_id", "general"))
#                         text = str(msg.get("text", ""))[:MAX_TEXT_LEN]
#                         if not text:
#                             continue
#                         now_ts = time.time()
#                         payload = {
#                             "type": "msg",
#                             "chat_id": chat_id,
#                             "from": name,
#                             "text": text,
#                             "ts": now_ts
#                         }
#                         if chat_id == "general":
#                             hub.broadcast(payload, exclude=name)
#                         else:
#                             members = []
#                             if db and db.is_configured():
#                                 members = db.get_chat_members(chat_id)
#                             hub.send_to_members(members, payload, exclude=name)
#
#                         if db and db.is_configured():
#                             threading.Thread(
#                                 target=db.save_message,
#                                 args=(name, text, now_ts, chat_id),
#                                 daemon=True
#                             ).start()
#
#                     elif kind == "get_chats":
#                         chats = db.get_user_chats(name) if db and db.is_configured() else []
#                         chan.send(json.dumps({"type": "chat_list", "chats": chats}).encode())
#
#                     elif kind == "get_users":
#                         users = db.get_all_users() if db and db.is_configured() else []
#                         chan.send(json.dumps({"type": "user_list", "users": users}).encode())
#
#                     elif kind == "get_history":
#                         chat_id = str(msg.get("chat_id", "general"))
#                         history = db.get_recent_messages(chat_id=chat_id, limit=50) if db and db.is_configured() else []
#                         chan.send(json.dumps({"type": "history", "chat_id": chat_id, "messages": history}).encode())
#
#                     elif kind == "create_direct_chat":
#                         target_user = str(msg.get("with_user", "")).strip()
#                         if db and db.is_configured():
#                             ok, res = db.get_or_create_direct_chat(name, target_user)
#                             if ok:
#                                 chat_for_creator = dict(res)
#                                 chat_for_creator["name"] = target_user
#                                 chan.send(json.dumps({"type": "chat_created", "chat": chat_for_creator}).encode())
#
#                                 chat_for_target = dict(res)
#                                 chat_for_target["name"] = name
#                                 hub.send_to(target_user, {"type": "chat_created", "chat": chat_for_target})
#                             else:
#                                 chan.send(json.dumps({"type": "error", "text": str(res)}).encode())
#                         else:
#                             chan.send(json.dumps({"type": "error", "text": "Supabase не настроен на сервере"}).encode())
#
#                     elif kind == "create_group_chat":
#                         group_name = str(msg.get("name", "")).strip()
#                         members = list(msg.get("members", []))
#                         if db and db.is_configured():
#                             ok, res = db.create_group_chat(name, group_name, members)
#                             if ok:
#                                 all_m = res.get("members", [])
#                                 for m in all_m:
#                                     hub.send_to(m, {"type": "chat_created", "chat": res})
#                             else:
#                                 chan.send(json.dumps({"type": "error", "text": str(res)}).encode())
#                         else:
#                             chan.send(json.dumps({"type": "error", "text": "Supabase не настроен на сервере"}).encode())
#
#                     elif kind == "who":
#                         hub.send_to(name, {"type": "system", "text": "В сети: " + ", ".join(hub.names())})
#                     elif kind == "quit":
#                         break
#                     # unknown message types are ignored
#
#             except ConnectionError:
#                 pass  # Клиент отключился
#             except (ValueError, InvalidTag, json.JSONDecodeError) as exc:
#                 print(f"[!] {name or peer}: {type(exc).__name__}: {exc}")
#             finally:
#                 if name:
#                     hub.remove(name)
#                     for change in hub.voice.leave(name):
#                         announce_voice(hub, *change)
#                     hub.broadcast({"type": "system", "text": f"{name} покинул чат"})
#                     print(f"[-] {name} вышел ({len(hub.names())} онлайн)")
#                 if chan is not None:
#                     chan.close()
#                 else:
#                     try:
#                         conn.close()
#                     except OSError:
#                         pass
#
#         # 2. first application message must be a join
#         hello = json.loads(chan.recv())
#         if hello.get("type") != "join" or not str(hello.get("name", "")).strip():
#             chan.send(json.dumps({"type": "error", "text": "expected a join message"}).encode())
#             return
#         name = str(hello["name"]).strip()[:MAX_NAME_LEN]
#         if hub.has(name):
#             chan.send(json.dumps({"type": "error", "text": f"name '{name}' is taken"}).encode())
#             return
#
#         hub.add(name, chan)
#         print(f"[+] {name} joined from {peer}  ({len(hub.names())} online)")
#         chan.send(json.dumps({"type": "welcome", "you": name, "online": hub.names()}).encode())
#         hub.broadcast({"type": "system", "text": f"{name} joined"}, exclude=name)
#
#         # 3. relay loop
#         while True:
#             msg = json.loads(chan.recv())
#             kind = msg.get("type")
#
#             if kind == "msg":
#                 text = str(msg.get("text", ""))[:MAX_TEXT_LEN]
#                 if not text:
#                     continue
#                 hub.broadcast(
#                     {"type": "msg", "from": name, "text": text, "ts": time.time()},
#                     exclude=name,
#                 )
#             elif kind == "who":
#                 hub.send_to(name, {"type": "system", "text": "online: " + ", ".join(hub.names())})
#             elif kind == "quit":
#                 break
#             # unknown message types are ignored
#
#     except (ConnectionError, ValueError, InvalidTag, json.JSONDecodeError) as exc:
#         print(f"[!] {name or peer}: {type(exc).__name__}: {exc}")
#     finally:
#         if name:
#             hub.remove(name)
#             hub.broadcast({"type": "system", "text": f"{name} left"})
#             print(f"[-] {name} left  ({len(hub.names())} online)")
#         if chan is not None:
#             chan.close()
#         else:
#             try:
#                 conn.close()
#             except OSError:
#                 pass
#
# def announce_voice(hub:Hub, chat_id,members:list[str],audience: list[str] | None):
#     payload = {"type": "voice_state", "chat_id": chat_id, "members": members}
#     if audience is None:
#         hub.broadcast(payload)
#     else:
#         hub.send_to_members(audience, payload)
#
#
#
#
# def main() -> None:
#     ap = argparse.ArgumentParser(description="encrypted chat relay server")
#     ap.add_argument("--host", default="127.0.0.1")
#     ap.add_argument("--port", type=int, default=9001)
#     args = ap.parse_args()
#
#     srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#     srv.bind((args.host, args.port))
#     srv.listen(64)
#     print(f"listening on {args.host}:{args.port}  (Ctrl-C to stop)")
#
#     hub = Hub()
#     try:
#         while True:
#             conn, addr = srv.accept()
#             conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
#             threading.Thread(
#                 target=serve_client, args=(conn, addr, hub), daemon=True
#             ).start()
#     except KeyboardInterrupt:
#         print("\nshutting down")
#     finally:
#         srv.close()
#
#
# if __name__ == "__main__":
#     main()
