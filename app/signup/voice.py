"""Turn signup logs into short Chinese lines for the refill console."""

from __future__ import annotations

import re

_RULES: list[tuple[str, str]] = [
    (r"批量启动 (\d+) 个任务（并发 (\d+)）", r"一共 \1 个小伙伴排队，\2 个一起出发"),
    (r"批量任务结束", "这一轮都忙完啦"),
    (r"批量已停止", "先暂停一下"),
    (r"收到停止请求", "收到，马上停下来"),
    (r"已在支付卡点，跳过.*", "这位已经在等付款，先跳过"),
    (r"任务异常:.*", "中途绊了一下，这位先放到失败里"),
    (r"开始补号：(\S+).*", r"开始帮 \1 办账号啦"),
    (r"防关联：同号间隔等待 (\d+)s", r"这位刚用过，先休息 \1 秒再继续"),
    (r"restart OK.*", "门开了，准备发验证码"),
    (r"send-otp OK.*", "验证码已经寄去邮箱了"),
    (r"邮箱自动取码成功.*", "在邮箱里捡到验证码啦"),
    (r"邮箱暂未取到验证码.*", "邮箱还在路上，验证码没到，等会儿再看"),
    (r"confirm-otp csrfFailed.*", "通行证过期了，重新进一次门"),
    (r"confirm-otp OK.*", "验证码对上了，去填小档案"),
    (r"profile 自动填充.*", "名字和生日填好了"),
    (r"profile 已写入", "小档案记下了"),
    (r"register-completion OK", "小档案交上去了"),
    (r"已激活账号，跳过 checkout", "这个账号已经激活，不用再验卡"),
    (r"到达 awaiting_external_payment.*", "走到付款门口了，等外部付完再继续"),
    (r"accept-tos OK.*", "服务条款同意好了"),
    (r"stateful-sharing OK", "登录状态同步好了"),
    (r"stateful-sharing 跳过.*", "登录状态同步先跳过，不影响后面"),
    (r"vm/wake OK", "账号已经被叫醒了"),
    (r"vm/wake 跳过.*", "叫醒这一步先跳过"),
    (r"已恢复激活会话.*", "找回刚才的登录状态了"),
    (r"auth/check:.*", "登录状态确认过了"),
    (r"auth/check 未通过.*", "登录状态还没通过，再试一次"),
    (r"准备入本池.*", "准备把账号领回家"),
    (r"入池失败.*", "领回家没成功，先记下来"),
    (r"注册成功.*", "注册成功，账号已经回家啦"),
    (r"卡池无可用卡.*", "卡包空了，等放进新卡再继续"),
    (r"卡池取卡.*", "从卡包里抽了一张卡"),
    (r"卡 .+ 异常.*", "这张卡出了点状况，换下一张"),
    (r"卡 .+ .*", "这张卡没通过，换下一张试试"),
    (r"checkout 全部失败.*", "验卡都没通过，这位先停一下"),
    (r"手工指定卡.*", "按你指定的卡去付款"),
    (r"手工触发卡池支付重试", "再去卡包抽一张试试"),
    (r"邮箱 refresh_token 已轮换并保存", "邮箱钥匙更新好了"),
    (r"跳过自动设密.*", "这次先不自动设密码"),
    (r"自动设密跳过：无邮箱", "没有邮箱，设不了密码"),
    (r"recovery-start OK.*", "找回密码的信寄出去了"),
    (r"自动设密失败：未取到恢复码.*", "没收到找回密码的信"),
    (r"自动设密失败.*", "自动设密码没成功"),
    (r"自动设密异常.*", "设密码时绊了一下"),
    (r"自动设密成功.*", "密码设好了，已经收起来"),
    (r"recovery 前 restart OK", "重新进门，准备找回密码"),
    (r"recovery-start 失败.*", "找回密码的信没发出去"),
    (r"recovery-confirm-code OK.*", "找回码对上了，接着设密码"),
    (r"recovery-confirm-code 失败.*", "找回码没对上"),
    (r"password login available.*", "这个邮箱可以密码登录"),
    (r"password-login 失败.*", "密码登录没成功"),
    (r"confirm-password OK", "密码确认通过"),
    (r"confirm-password 失败.*", "密码没确认上"),
    (r"invite redeem OK.*", "邀请码用上了"),
    (r"cookie 逃生舱写入", "登录小饼干先存好了"),
    (r"resume payment_ref.*", "接着上次的付款往下走"),
    (r"代理这条小路堵住了.*", r"\g<0>"),
    (r"协议错误:.*proxy.*|协议错误:.*transport.*|协议错误:.*Connect.*|协议错误:.*Timeout.*|协议错误:.*timed out.*", "代理还是走不通，这位先放到失败里"),
    (r"协议错误:.*", "站点没答应，这位先放到失败里"),
    (r"异常:.*", "出了点意外，这位先放到失败里"),
]


def soften(msg: str) -> str:
    """Map one stored log line to a Chinese sentence. Unknown lines pass through."""
    text = (msg or "").strip()
    if not text:
        return text
    item = ""
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", text)
    if m:
        item, text = m.group(1), m.group(2)
    for pattern, repl in _RULES:
        if re.search(pattern, text, flags=re.I):
            text = re.sub(pattern, repl, text, count=1, flags=re.I)
            break
    else:
        text = re.sub(r"https?://\S+|socks5h?://\S+", "代理", text)
        text = re.sub(r"\s+", " ", text).strip()
    if item:
        return f"{text} · {item[-6:]}"
    return text
