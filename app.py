
import os, sys, time, json, requests
import urllib.parse

API_KEY    = os.environ.get("API_KEY") or ""
SERVER_ID  = os.environ.get("SERVER_ID") or ""
COOKIE_RAW = os.environ.get("COOKIE") or ""
EMAIL      = os.environ.get("EMAIL") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://panel.host-ship.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20

MAX_RENEWAL_DAYS = 14

def mask(s: str, head: int = 6, tail: int = 4) -> str:
    s = s or ""
    return f"{s[:head]}...{s[-tail:]}" if len(s) > head + tail else "***"

def mask_email(addr: str) -> str:
    addr = (addr or "").strip()
    if not addr:
        return "（未填）"
    if "@" in addr:
        name, domain = addr.split("@", 1)
        if len(name) <= 4:
            return f"{name}@{domain}"
        return f"{name[:2]}****{name[-2:]}@{domain}"
    return addr[:2] + "****" if len(addr) > 2 else "***"

def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT_ID, "text": message,
                            "parse_mode": "HTML"}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")

def format_notification(status: str, server_name: str = "", old: str = "",
                        new: str = "", error: str = "", reason: str = "") -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    lines = ["🎰 Host-ship 续期报告", "", f"{status}",
             f"📧 账号: {mask_email(EMAIL)}"]
    if server_name:
        lines.append(f"🖥️ 服务器: {server_name}")
    if new:
        lines.append(f"⏱ 续期前到期时间: {old or '（未获取到）'}")
        lines.append(f"⏱ 续期后到期时间: {new}")
    elif old:
        lines.append(f"⏱ 续期前到期时间: {old}")
    if reason:
        lines.append(f"📝 原因: {reason}")
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"🕒 执行时间: {now}(北京时间)")
    return "\n".join(lines)

def parse_cookie_str(raw: str):
    pairs = []
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name, value = name.strip(), value.strip()
        if name and value:
            pairs.append((name, value))
    return pairs

def pick_snapshot(attrs: dict):
    if not isinstance(attrs, dict):
        return "（未知）", "（未知）", "（未获取到）", {}
    name = attrs.get("name") or attrs.get("server_name") or "（未知）"
    status = attrs.get("status") or attrs.get("state") or ""
    if isinstance(status, dict):
        status = status.get("status") or status.get("state") or ""
    renewal = attrs.get("renewal")
    try:
        renewal = int(renewal) if renewal is not None else None
    except (TypeError, ValueError):
        renewal = None
    try:
        cooldown = int(attrs.get("renewal_cooldown") or 0)
    except (TypeError, ValueError):
        cooldown = 0
    gate = {"renewable": attrs.get("renewable"),
            "renewal": renewal,
            "renewal_cooldown": cooldown,
            "renewal_locked": attrs.get("renewal_locked")}
    if renewal is not None:
        old = f"剩余{renewal}天"
    else:
        old = str(status) if status else "（未获取到）"
    return str(name), str(status or "（未知）"), str(old), gate

def should_skip_renew(gate: dict):
    if not isinstance(gate, dict) or not gate:
        return False, ""
    renewal = gate.get("renewal")
    if isinstance(renewal, int) and renewal >= MAX_RENEWAL_DAYS:
        return True, f"Renew Limit Reached（renewal={renewal}，已达{MAX_RENEWAL_DAYS}天上限，无需续期）"
    if gate.get("renewable") is False:
        return True, "后端标记 renewable=false，无需续期"
    if gate.get("renewal_locked") is True:
        return True, "renewal_locked=true，已锁定，无需续期"
    cd = gate.get("renewal_cooldown") or 0
    if isinstance(cd, int) and cd > 0:
        return True, f"冷却中（renewal_cooldown={cd}），暂无需续期"
    return False, ""

LIMIT_TEXT = "renew limit reached"
# 面板在已达续期上限时的另一套文案（Jexactyl DisplayException）
CAP_TEXT = "cannot add more than"

def contains_limit_text(text: str) -> bool:
    low = (text or "").lower()
    nospace = " ".join(low.split())
    return LIMIT_TEXT in nospace or "renew limit" in nospace

def contains_cap_text(text: str) -> bool:
    nospace = " ".join((text or "").lower().split())
    return CAP_TEXT in nospace

def is_limit_error(info: str) -> bool:
    if contains_limit_text(info) or contains_cap_text(info):
        return True
    low = (info or "").lower()
    return any(k in low for k in ("limit", "maximum", "max", "cooldown",
                                  "locked", "renewable", "too many"))

def api_headers():

    return {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest", "User-Agent": UA}

def api_get_server():
    try:
        r = requests.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}",
                         headers=api_headers(), timeout=TIMEOUT)
        if r.status_code == 200:
            if contains_limit_text(r.text):
                print("⏭️ GET 响应里检测到 Renew Limit Reached，直接跳过")
                name, status, old, gate = pick_snapshot({})
                try:
                    attrs = r.json().get("attributes", {})
                    name, status, old, gate = pick_snapshot(attrs if isinstance(attrs, dict) else {})
                except Exception:
                    pass
                gate = dict(gate or {})
                gate["renewal"] = MAX_RENEWAL_DAYS
                return True, (name, status, old, gate), ""
            try:
                attrs = r.json().get("attributes", {})
            except Exception:
                attrs = {}
            return True, pick_snapshot(attrs), ""
        return False, ("（未知）", "（未知）", "（未获取到）", {}), f"GET {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, ("（未知）", "（未知）", "（未获取到）", {}), f"GET 异常: {e}"

def cookie_get_server(s: requests.Session):
    try:
        g = s.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}", timeout=TIMEOUT)
        print(f"📡 备链 GET → HTTP {g.status_code}")
        if g.status_code == 200:
            try:
                attrs = g.json().get("attributes", {})
            except Exception:
                attrs = {}
            return True, pick_snapshot(attrs), ""
        if g.status_code in (401, 403):
            return False, ("（未知）", "（未知）", "（未获取到）", {}), f"备链 Cookie 已失效（GET {g.status_code}）"
        return False, ("（未知）", "（未知）", "（未获取到）", {}), f"备链 GET {g.status_code}: {g.text[:200]}"
    except Exception as e:
        return False, ("（未知）", "（未知）", "（未获取到）", {}), f"备链 GET 异常: {e}"

def page_has_limit_text(session: requests.Session = None) -> bool:
    if not COOKIE_RAW:
        return False
    try:
        short_id = (SERVER_ID or "").split("-")[0]
        if not short_id:
            return False
        s = session or requests.Session()
        if session is None:
            for name, value in parse_cookie_str(COOKIE_RAW):
                try:
                    s.cookies.set(name, value, domain="panel.host-ship.com", path="/")
                except Exception:
                    pass
        h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
             "Referer": f"{BASE_URL}/"}
        r = s.get(f"{BASE_URL}/server/{short_id}", headers=h, timeout=TIMEOUT)
        if r.status_code == 200 and contains_limit_text(r.text):
            print("⏭️ 服务器页面检测到 Renew Limit Reached，直接跳过")
            return True
    except Exception as e:
        print(f"⚠️ 页面上限探测失败（忽略，走 API 门控）: {e}")
    return False

def api_renew():
    try:
        r = requests.post(f"{BASE_URL}/api/client/servers/{SERVER_ID}/renew",
                          headers=api_headers(), timeout=TIMEOUT)
        print(f"📡 POST renew → HTTP {r.status_code}")
        if r.status_code in (200, 201, 202, 204):
            return True, ""
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, f"POST 异常: {e}"

def cookie_renew():
    pairs = parse_cookie_str(COOKIE_RAW)
    if not pairs:
        return False, "COOKIE 为空或格式错误"
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json",
                      "Referer": f"{BASE_URL}/"})
    for name, value in pairs:
        try:
            s.cookies.set(name, value, domain="panel.host-ship.com", path="/")
        except Exception as e:
            print(f"⚠️ 装配 Cookie {name} 失败: {e}")
    try:
        g = s.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}", timeout=TIMEOUT)
        print(f"📡 备链 GET → HTTP {g.status_code}")
        if g.status_code in (401, 403):
            return False, f"备链 Cookie 已失效（GET {g.status_code}）"
        if g.status_code == 200:
            if contains_limit_text(g.text):
                return False, "Renew Limit Reached，无需续期（备链 GET 响应里检测到上限文本）"
            try:
                attrs = g.json().get("attributes", {})
            except Exception:
                attrs = {}
            _n, _st, _old, _gate = pick_snapshot(attrs if isinstance(attrs, dict) else {})
            print(f"📋 备链快照：{_n} / {_st} / {_old} / {_gate}")
            _skip, _reason = should_skip_renew(_gate)
            if _skip:
                return False, f"Renew Limit Reached，无需续期（{_reason}）"
        xsrf = s.cookies.get("XSRF-TOKEN", domain="panel.host-ship.com") or ""
        try:
            xsrf = urllib.parse.unquote(xsrf)
        except Exception:
            pass

        headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf
        r = s.post(f"{BASE_URL}/api/client/servers/{SERVER_ID}/renew",
                   headers=headers, timeout=TIMEOUT)
        print(f"📡 备链 POST renew → HTTP {r.status_code}")
        if r.status_code in (200, 201, 202, 204):
            return True, ""
        if r.status_code in (401, 403, 419):
            return False, f"备链认证失败（HTTP {r.status_code}），请重拷 COOKIE"
        return False, f"备链 HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, f"备链异常: {e}"

def main():
    print("#" * 25)
    print("   Host-ship 自动续期（纯 HTTP）")
    print("#" * 25)
    if not API_KEY and not COOKIE_RAW:
        print("ℹ️ 未配置 API_KEY 且无 COOKIE，脚本终止。")
        sys.exit(1)
    if not SERVER_ID:
        print("ℹ️ 未配置 SERVER_ID（服务器 UUID），脚本终止。")
        sys.exit(1)
    print(f"🎯 目标服务器: {mask(SERVER_ID, 8, 4)}")
    print(f"🔑 主链 API_KEY: {'已配置' if API_KEY else '未配置'} / "
          f"备链 COOKIE: {'已配置' if COOKIE_RAW else '未配置'}")

    used_backup = False

    name, status, old, gate = "（未知）", "（未知）", "（未获取到）", {}
    if API_KEY:
        ok, snap, err = api_get_server()
        if ok:
            name, status, old, gate = snap
            print(f"📋 主链快照：{name} / {status} / {old} / {gate}")
        else:
            print(f"⚠️ 主链 GET 失败: {err}")
    skip, reason = should_skip_renew(gate)
    if skip:
        print(f"⏭️ 无需续期: {reason}")
        send_telegram_message(format_notification("⏭️ 续期时间未到", server_name=name,
                                                  old=old))
        print("🏁 执行完毕：无需续期")
        return

    if page_has_limit_text():
        reason = "页面检测到 Renew Limit Reached，无需续期"
        print(f"⏭️ 无需续期: {reason}")
        send_telegram_message(format_notification("⏭️ 续期时间未到", server_name=name,
                                                  old=old))
        print("🏁 执行完毕：无需续期")
        return

    ok, info = (False, "未执行主链（无 API_KEY）")
    if API_KEY:

        ok, info = api_renew()
        if not ok and is_limit_error(info):
            print(f"⏭️ 服务端提示已达上限/冷却，视为无需续期: {info}")
            send_telegram_message(format_notification("⏭️ 续期时间未到", server_name=name,
                                                      old=old))
            print("🏁 执行完毕：无需续期")
            return

    used_backup = False
    if not ok and COOKIE_RAW and ("401" in info or "403" in info or "未执行" in info):
        print("🔄 主链认证失败，切换备链 Cookie 重试...")
        ok, info = cookie_renew()
        used_backup = True
        if not ok and is_limit_error(info):
            print(f"⏭️ 服务端提示已达上限/冷却，视为无需续期: {info}")
            send_telegram_message(format_notification("⏭️ 续期时间未到", server_name=name,
                                                      old=old))
            print("🏁 执行完毕：无需续期")
            return

    if ok:

        new = old
        if API_KEY:
            ok2, snap2, _ = api_get_server()
            if ok2:
                name, status, new, _gate2 = snap2
        via = "备链 Cookie" if used_backup else "主链 API"
        print(f"✅ 续期成功（{via}），{old} → {new}")
        send_telegram_message(format_notification("✅ 续期成功", server_name=name,
                                                  old=old, new=new if new != old else f"{new}（已确认）"))
        print("🏁 执行完毕：续期成功")
        return

    print(f"❌ 续期失败: {info}")
    send_telegram_message(format_notification("❌ 续期失败", server_name=name,
                                              old=old, error=str(info)))
    print("🏁 执行完毕：续期失败")
    sys.exit(1)

if __name__ == "__main__":
    main()
