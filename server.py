import websockets
import asyncio
import os




CLIENTS = {

}

async def broadcast(message):
   if CLIENTS:
       await asyncio.gather(*[client.send(message) for client in CLIENTS])

async def handle_client(websocket):
    try:
        alias = await websocket.recv()
        CLIENTS[websocket] = alias
        join_msg = f"{alias} присоединился к чату!"
        print(join_msg)
        await broadcast(join_msg)

        async for message in websocket:
            full_message = f"{alias}: {message}"
            await broadcast(full_message)
    except websockets.exceptions.ConnectionClosed:
        ...
    finally:
        if websocket in CLIENTS:
            alias = CLIENTS[websocket]
            del CLIENTS[websocket]
            leave_msg = f"🚪 {alias} покинул чат."
            print(leave_msg)
            await broadcast(leave_msg)

async def main():
    port = os.environ.get("PORT", 29548)
    async with websockets.serve(handle_client, "0.0.0.0", port):
        print(f"WebSocket сервер запущен на порту {port}...")
        await asyncio.Future()
if __name__ == '__main__':
    asyncio.run(main())