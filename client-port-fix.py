import asyncio
import queue
import threading
import socket
import websockets
import customtkinter as ctk
from tkinter import messagebox
from datetime import datetime
import traceback

ctk.set_appearance_mode("Dark")


class ChatApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("✨ Мессенджер")
        self.geometry("540x680")
        self.minsize(420, 550)
        self.configure(fg_color="#1c1c1e")

        self.alias = ""
        self.ws = None
        self.running = True
        self.message_queue = queue.Queue()

        # Асинхронный цикл в отдельном потоке
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

        # ----- Фрейм входа -----
        self.login_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.login_frame.pack(pady=60, padx=30, fill="both", expand=True)

        ctk.CTkLabel(self.login_frame, text="Добро пожаловать", font=("Segoe UI", 28, "bold")).pack(pady=(0, 20))
        ctk.CTkLabel(self.login_frame, text="Введите ваше имя", font=("Segoe UI", 14)).pack()

        self.alias_entry = ctk.CTkEntry(self.login_frame, placeholder_text="Никнейм",
                                        width=300, height=40, corner_radius=12,
                                        font=("Segoe UI", 14))
        self.alias_entry.pack(pady=15)
        self.alias_entry.bind("<Return>", lambda e: self.connect_to_server())

        self.connect_btn = ctk.CTkButton(self.login_frame, text="Подключиться",
                                         width=200, height=45, corner_radius=12,
                                         font=("Segoe UI", 14, "bold"),
                                         fg_color="#6e15c2", hover_color="#8a2be2",
                                         command=self.connect_to_server)
        self.connect_btn.pack(pady=10)

        self.status_label = ctk.CTkLabel(self.login_frame, text="", font=("Segoe UI", 12))
        self.status_label.pack(pady=5)

        # ----- Фрейм чата -----
        self.chat_frame = ctk.CTkFrame(self, fg_color="transparent")

        self.header_frame = ctk.CTkFrame(self.chat_frame, fg_color="#2c2c2e", height=50, corner_radius=0)
        self.header_frame.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(self.header_frame, text="💬 Общий чат", font=("Segoe UI", 16, "bold")).pack(pady=10)

        self.chat_display = ctk.CTkTextbox(self.chat_frame, wrap="word",
                                           font=("Segoe UI", 13),
                                           scrollbar_button_color="#6e15c2",
                                           scrollbar_button_hover_color="#8a2be2",
                                           corner_radius=10,
                                           fg_color="#1c1c1e")
        self.chat_display.pack(pady=(0, 8), padx=10, fill="both", expand=True)
        self.chat_display.configure(state="disabled")

        self.chat_display.tag_config("mine",
                                     justify="right",
                                     foreground="#ffffff",
                                     background="#6e15c2",
                                     spacing3=8,
                                     rmargin=10,
                                     lmargin1=80,
                                     lmargin2=80)
        self.chat_display.tag_config("theirs",
                                     justify="left",
                                     foreground="#000000",
                                     background="#e0e0e0",
                                     spacing3=8,
                                     lmargin1=10,
                                     lmargin2=10,
                                     rmargin=80)
        self.chat_display.tag_config("system",
                                     justify="center",
                                     foreground="#888888",
                                     spacing3=5)

        self.input_frame = ctk.CTkFrame(self.chat_frame, fg_color="transparent")
        self.input_frame.pack(pady=(0, 12), padx=10, fill="x")

        self.msg_entry = ctk.CTkEntry(self.input_frame, placeholder_text="Напишите сообщение...",
                                      height=42, corner_radius=15,
                                      font=("Segoe UI", 13))
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.msg_entry.bind("<Return>", lambda event: self.send_message())

        self.send_btn = ctk.CTkButton(self.input_frame, text="➤",
                                      width=50, height=42, corner_radius=15,
                                      font=("Segoe UI", 18, "bold"),
                                      fg_color="#6e15c2", hover_color="#8a2be2",
                                      command=self.send_message)
        self.send_btn.pack(side="right")

        self.msg_entry.configure(state="disabled")
        self.send_btn.configure(state="disabled")

        self.after(100, self.process_queue)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # ---------- Подключение ----------
    def connect_to_server(self):
        alias = self.alias_entry.get().strip()
        if not alias:
            messagebox.showerror("Ошибка", "Введите ваше имя!")
            return

        self.alias = alias
        self.status_label.configure(text="Подключение...", text_color="#ffcc00")
        self.connect_btn.configure(state="disabled")

        # Запускаем асинхронное подключение
        asyncio.run_coroutine_threadsafe(self.start_client(), self.loop)

    async def start_client(self):
        try:
            # Попробуем проверить, открыт ли порт (быстрая проверка)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", 29548))
            sock.close()
            if result != 0:
                self.after(0, lambda: self.status_label.configure(
                    text="❌ Сервер не доступен (порт 29548 закрыт)", text_color="#ff4444"))
                self.after(0, lambda: self.connect_btn.configure(state="normal"))
                return

            self.ws = await websockets.connect("ws://127.0.0.1:29548")
            await self.ws.send(self.alias)  # отправляем псевдоним

            # Переключаем интерфейс в главном потоке
            self.after(0, self.switch_to_chat)

            # Запускаем приём сообщений
            await self.receive_messages()

        except Exception as e:
            error_text = f"{e}\n{traceback.format_exc()}"
            self.after(0, lambda: self.status_label.configure(
                text=f"❌ Ошибка: {e}", text_color="#ff4444"))
            self.after(0, lambda: self.connect_btn.configure(state="normal"))
            if self.ws:
                await self.ws.close()
                self.ws = None
            # Выводим полную ошибку в консоль для диагностики
            print("Ошибка подключения:", error_text)

    def switch_to_chat(self):
        self.login_frame.pack_forget()
        self.chat_frame.pack(pady=0, padx=0, fill="both", expand=True)
        self.msg_entry.configure(state="normal")
        self.send_btn.configure(state="normal")
        self.msg_entry.focus()
        self.add_message("[Система]", "Подключено к серверу! Добро пожаловать.", is_system=True)

    # ---------- Приём сообщений ----------
    async def receive_messages(self):
        try:
            async for message in self.ws:
                self.message_queue.put(message)
        except websockets.exceptions.ConnectionClosed as e:
            self.message_queue.put(f"[Система] Соединение закрыто: {e}")
        except Exception as e:
            self.message_queue.put(f"[Система] Ошибка получения: {e}")
        finally:
            self.after(0, self.disable_chat_input)
            if self.ws:
                await self.ws.close()
                self.ws = None

    # ---------- Обработка очереди сообщений ----------
    def process_queue(self):
        try:
            while True:
                msg = self.message_queue.get_nowait()
                # Если сообщение начинается с нашего алиаса + ":" – это эхо, пропускаем
                if msg.startswith(self.alias + ":"):
                    continue
                elif ":" in msg:
                    # Сообщение от другого участника
                    self.add_message("", msg, is_mine=False, is_system=False)
                else:
                    # Системное сообщение (присоединился / покинул)
                    self.add_message("", msg, is_system=True)
        except queue.Empty:
            pass
        finally:
            if self.running:
                self.after(100, self.process_queue)

    # ---------- Добавление сообщения в чат ----------
    def add_message(self, sender="", text="", is_mine=False, is_system=False):
        self.chat_display.configure(state="normal")
        if is_system:
            self.chat_display.insert("end", f" {text} \n", "system")
        else:
            self.chat_display.insert("end", f"{text}\n", "mine" if is_mine else "theirs")
        self.chat_display.see("end")
        self.chat_display.configure(state="disabled")

    # ---------- Отправка сообщения ----------
    def send_message(self):
        if not self.ws:
            messagebox.showerror("Ошибка", "Нет подключения к серверу.")
            return

        text = self.msg_entry.get().strip()
        if not text:
            return

        timestamp = datetime.now().strftime("%H:%M")
        full_display = f"{self.alias} [{timestamp}]: {text}"
        self.add_message("", full_display, is_mine=True)

        # Отправляем серверу только текст (алиас добавит сервер)
        asyncio.run_coroutine_threadsafe(self.ws.send(text), self.loop)
        self.msg_entry.delete(0, "end")

    # ---------- Блокировка ввода при отключении ----------
    def disable_chat_input(self):
        self.msg_entry.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        self.add_message("[Система]", "Отключено от сервера.", is_system=True)

    # ---------- Закрытие приложения ----------
    def on_closing(self):
        self.running = False
        if self.ws:
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        self.destroy()


if __name__ == "__main__":
    app = ChatApp()
    app.mainloop()