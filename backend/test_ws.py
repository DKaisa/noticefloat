"""端到端 WS 测试：Bob 连 WS，Alice 发任务，看 Bob 是否收到推送。"""
import asyncio
import json
import httpx
import websockets

BASE = "http://127.0.0.1:8787"
WS_BASE = "ws://127.0.0.1:8787"

ALICE = "dev-alice-uuid-001"
BOB = "dev-bob-uuid-002"

async def main():
    async with httpx.AsyncClient() as http:
        # 找一个已有群（我们在 REST 测试里建的）
        r = await http.get(f"{BASE}/api/devices/{BOB}/groups")
        groups = r.json()["groups"]
        assert groups, "Bob 还没有加入任何群，请先跑一次 REST 建群/加群"
        code = groups[0]["code"]
        print(f"使用群: {code} · {groups[0]['name']}")

        # Bob 打开 WS 监听
        bob_received = asyncio.Event()
        payloads = []
        async def bob_ws():
            async with websockets.connect(f"{WS_BASE}/ws/{BOB}") as ws:
                print("Bob WS 已连接")
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=8)
                        payloads.append(msg)
                        print("Bob 收到:", msg)
                        bob_received.set()
                except asyncio.TimeoutError:
                    print("Bob 8s 内未收到消息，退出")

        bob_task = asyncio.create_task(bob_ws())
        await asyncio.sleep(0.5)  # 等 WS 建立

        # Alice 发任务
        r = await http.post(
            f"{BASE}/api/groups/{code}/tasks",
            json={"device_id": ALICE, "title": "WS 推送测试", "content": "Bob 应收到", "remind_at": 0},
        )
        print("Alice 发布结果:", r.json())

        # 等 Bob 收到
        try:
            await asyncio.wait_for(bob_received.wait(), timeout=5)
            print("✅ 端到端推送成功")
        except asyncio.TimeoutError:
            print("❌ Bob 未在 5s 内收到推送")

        bob_task.cancel()
        try:
            await bob_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    asyncio.run(main())
