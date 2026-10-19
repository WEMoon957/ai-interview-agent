"""命令行发起「妙记设备授权」，适合在服务器上给某位店长/HR 一次性开通。

用法（在 api 容器内执行）::

    docker exec -it interview-agent-api python scripts/authorize_minutes.py

流程：
1. 脚本向飞书申请设备码，打印授权链接与验证码；
2. 把链接发给本人，对方在飞书里打开并确认（600 秒内有效）；
3. 脚本轮询直到授权成功，把 user_access_token / refresh_token 落库；
4. 之后系统在拉取该用户的妙记时会自动使用其 user token（私有妙记也能读）。

授权有效期：access_token 约 2 小时（自动刷新），refresh_token 约 30 天；
过期后重新执行本脚本，或在后台「妙记授权」页重新生成链接。
"""
import sys
import time

from app.config import get_settings
from app.db.session import SessionLocal
from app.services.minutes_auth import (
    poll_device_authorization,
    start_device_authorization,
)


def main() -> int:
    settings = get_settings()
    db = SessionLocal()
    try:
        ok, data, err = start_device_authorization(settings, authorized_by="cli")
        if not ok:
            print(f"申请设备码失败：{err}")
            return 1

        print("=" * 60)
        print("请让本人在飞书中打开下面的链接并点击「授权」：")
        print(data.get("verification_uri_complete") or data.get("verification_uri"))
        print(f"验证码：{data.get('user_code')}")
        print(f"有效期：{data.get('expires_in')} 秒（轮询间隔 {data.get('interval')} 秒）")
        print("=" * 60)

        interval = max(2, int(data.get("interval") or 5))
        deadline = time.time() + int(data.get("expires_in") or 600)
        while time.time() < deadline:
            time.sleep(interval)
            status, auth, message = poll_device_authorization(
                db, data["session_id"], settings
            )
            if status == "pending":
                continue
            if status == "success":
                print(f"授权成功：{auth.get('name')}（{auth.get('open_id')}）")
                return 0
            print(f"授权未完成：{status} {message}")
            return 1

        print("授权超时，请重新执行本脚本。")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
