# -*- coding: utf-8 -*-
"""运行时消息国际化（中文 / 英文）。

配置项 i18n_settings.language 控制回复语言；缺省回退中文。
WebUI 文案的国际化请见 .astrbot-plugin/i18n/*.json。
"""

_TRANSLATIONS = {
    "zh": {
        # 通用
        "common.at_or_id_required": "请 @ 对方，或输入网站ID/QQ号。",
        "common.amount_required": "请提供要调整的额度。",
        "common.unknown": "未知",
        "common.unexpected_error": "❌ 处理命令时发生未预期错误：{err}",
        # 未绑定
        "not_bound": "您尚未绑定网站ID，无法进行此操作。\n请发送「绑定 [您的网站ID]」指令完成绑定。",
        # pingapi
        "ping.connected": "✅ 已连接",
        "ping.disconnected": "❌ 连接失败",
        "ping.running": "🎉 Pong! NewAPI 插件套件 V{version} 正在运行！",
        "ping.db_engine": "数据库引擎",
        "ping.db_status": "数据库状态",
        "ping.api_status": "New API 状态",
        # 指令大全
        "help.header": "📚 中转站指令大全（NewAPI 套件 V{version}）\n====================\n",
        "help.body": (
            "【账户】\n"
            "绑定 网站ID —— 绑定你的网站账号\n"
            "查询余额 —— 查自己的余额\n"
            "签到 —— 每日签到领额度\n"
            "查余额 网站ID（管理员）—— 查他人余额\n"
            "查询 网站ID/QQ/OpenID（管理员）—— 查绑定\n"
            "查ID 用户名/邮箱 —— 查网站用户ID\n"
            "调整余额 网站ID 金额（管理员）—— 正数加/负数减\n"
            "解绑 网站ID（管理员）\n"
            "\n【娱乐】\n"
            "打劫 网站ID 或 @成员 —— 打劫一次\n"
            "PK 网站ID 金额 —— 发起 PK（5 分钟内应战；挑战管理员自动应战）\n"
            "接受PK [发起人网站ID] —— 应战；时间戳数字和取尾数：双=挑战者胜、单=应战者胜\n"
            "PK榜 —— 今日 PK 盈亏榜\n"
            "PK战绩 [网站ID] —— PK 数据（他人仅管理员）\n"
            "PK总战绩 [网站ID] —— PK 历史总数据（他人仅管理员）\n"
            "醍醐测智 —— 使用配置的模型生成 SVG 动画并截图；定时推送在插件配置中设置，钉钉开启加签时需填 SEC 密钥\n"
            "醍醐测智（多站点批量）—— 在插件配置 → Codex 生图 → 多站点列表填写「站点地址|APIKey|模型名」或「名称|站点地址|APIKey|模型名」，一行一个站点；\n"
            "　　同一条命令即批量测智：逐站点出图并逐张发送，单个站点失败不影响其他站点；最多 10 个站点，坏行跳过并提示（密钥脱敏）；列表留空则回到单站点模式\n"
            "钉钉图片 —— 在图片存储配置中启用 MinIO/S3，PNG 上传并验证公网访问后显示图片；失败会提示并退回文字摘要\n"
            "图片存储诊断 —— 日志仅记录安全错误码；storage_full 表示存储空间不足，请管理员清理或扩容；不会自动重试或放宽公网访问校验\n"
            "QQ 定时推送 —— 插件配置的 QQ 推送群号列表可添加多个群；清空即停止 QQ 推送；图片说明附生成时间（北京时间 UTC+8，同轮各群一致）\n"
            "醍醐推送会话 —— 在目标群获取可直接填写的推送会话\n"
            "醍醐推送绑定 群号（AstrBot 管理员）—— 在对应群绑定普通群号与官方群会话；绑定不自动订阅\n"
            "醍醐推送解绑 群号 / 醍醐推送状态（AstrBot 管理员）—— 解除绑定或检查配置；官方主动消息仍受平台权限与配额限制\n"
            "醍醐推送状态还可查看最近任务阶段、失败原因与耗时；模型请求默认最多等待 10 分钟，超时后下个周期继续\n"
            "榜单 —— 群余额榜与打劫榜\n"
            "消耗榜 —— 全站 Token 消耗榜\n"
            "\n【红包】\n"
            "发红包 份数 总额度（管理员）\n"
            "抢红包 红包代码\n"
            "个人红包 份数 总额度 —— 从自己余额发\n"
            "验证令牌 网站访问令牌 —— 个人红包首次需验证\n"
            "红包诊断（管理员）\n"
            "\n【数据与诊断】\n"
            "new-tx 导出 / new-tx 导入（管理员，导入覆盖数据）\n"
            "pingapi —— 数据库与 New API 连接状态\n"
            "提及诊断 —— QQ 成员 @只读检查\n"
            "\n【中转站健康度（模型情况插件）】\n"
            "模型情况 —— 模型健康度图卡\n"
            "模型情况 <主题> / 模型情况主题\n"
            "模型情况会话（管理员）\n"
            "\n指令无需 @机器人，直接发送即可。"
        ),
        # 查询余额（本人）
        "query_balance.failed": "查询失败，无法从网站获取您的余额信息。请稍后再试或联系管理员。",
        "query_balance.success": "查询成功！\n--------------------\n您绑定的网站ID: {site_id}\n当前剩余额度: {quota}",
        # 查余额（管理员）
        "query_other.not_found": "❌ 查询失败：未在绑定记录中找到与 {id} 相关的任何信息。",
        "query_other.failed": "查询失败，无法从网站获取该用户的余额信息。请稍后再试或联系管理员。",
        "query_other.label_website": "网站ID",
        "query_other.label_qq": "QQ号",
        "query_other.success": "✅ 查询成功！\n--------------------\n输入类型: {label}\n{label}: {id}\n绑定的网站ID: {site_id}\n当前剩余额度: {quota}",
        # 绑定
        "bind.id_required": "请提供要绑定的网站ID。\n用法：绑定 [你的网站ID]（纯数字），例如：绑定 1234",
        "bind.id_invalid": "❌ 网站ID应为纯数字，你输入的「{input}」无法识别。\n请检查后重试，例如：绑定 1234",
        "bind.validating": "验证通过，执行绑定...",
        "bind.already_bound": "您好，您的QQ已经与网站ID {site_id} 签订了契约，无需重复绑定。",
        "bind.qq_level_low": "抱歉，您的QQ等级({level})未达到所要求的 {min_level} 级，暂时无法绑定。",
        "bind.api_user_not_found": "审核失败：网站中不存在ID为 {site_id} 的用户，请检查您的ID。",
        "bind.website_blacklisted": "审核失败：网站ID {site_id} 已被管理员列入禁止绑定名单，无法绑定。",
        "bind.user_blacklisted": "审核失败：您的QQ {qq} 已被管理员列入禁止绑定名单，无法绑定。",
        "bind.id_taken": "审核失败：ID {site_id} 已被另一位用户绑定，无法操作。",
        "bind.success": "恭喜您！绑定成功！\n您的QQ现已与网站ID {site_id} 绑定。\n已自动为您晋升至【{group}】分组。",
        "bind.openid_success": "恭喜您！绑定成功！\n您的OpenID {openid} 现已与网站ID {site_id} 绑定。\n已自动为您晋升至【{group}】分组。",
        "bind.openid_success_no_group": "恭喜您！绑定成功！\n您的OpenID {openid} 现已与网站ID {site_id} 绑定。\nℹ️ 已开启「仅野机绑定改分组」：官机绑定不改变用户组。",
        "bind.openid_need_qq": "❌ 绑定失败：当前已开启「官机绑定需先绑QQ」，网站ID {site_id} 还没有 QQ 号（野机）绑定。\n请先在野机机器人处完成「绑定 {site_id}」后再来。",
        "bind.failed": "绑定过程中发生未知错误，操作已自动撤销，请联系管理员。",
        # 签到
        "check_in.disabled": "抱歉，每日签到功能当前未开启。",
        "check_in.already": "您今天已经签过到了，请明天再来吧！",
        "check_in.api_user_not_found": "签到失败：无法获取您的网站用户信息，请联系管理员。",
        "check_in.api_update_failed": "签到失败：向网站服务器更新额度时发生错误，请稍后再试。",
        "check_in.unknown": "签到时发生未知错误，请联系管理员。",
        # 解绑
        "unbind.success": "✅ 操作成功！\n已将网站ID: {site_id}\n从QQ用户: {qq} 的契约中解放。",
        "unbind.not_found": "❌ 操作无效：未找到网站ID {site_id} 的绑定记录。",
        "unbind.failed": "❌ 操作失败：在为网站ID {site_id} 执行净化时发生未知错误，请检查后台日志。",
        # 查询（管理员）
        "lookup.website": "✅ 查询成功！输入的是【网站ID】\n--------------------\n网站ID: {site_id}\n已绑定至QQ: {qq}\n绑定时间: {time}",        "lookup.qq": "✅ 查询成功！输入的是【QQ号】\n--------------------\nQQ号: {qq}\n已绑定至网站ID: {site_id}\n绑定时间: {time}",
        "lookup.not_found": "❌ 查询失败：未在绑定记录中找到与 {id} 相关的任何信息。",
        "lookup.openid_extra": "\n🔗 另有 OpenID 绑定（官机）：{openid}",
        # 查ID（按用户名/邮箱搜索网站用户，所有用户可用，邮箱打码）
        "search.usage": "用法：查ID 用户名或邮箱\n例如：查ID 张三 或 查ID zhangsan@example.com",
        "search.header": "🔍 按「{keyword}」查到的网站用户（最多 5 条）：",
        "search.line": "ID: {user_id}｜用户名: {username}｜邮箱: {email}",
        "search.not_found": "❌ 没有找到与「{keyword}」匹配的用户。",
        "search.failed": "❌ 用户搜索接口调用失败，请稍后再试或联系管理员。",
        # 调整余额
        "adjust.success_inc": "✅ 操作成功！\n--------------------\n目标用户ID: {site_id}\n已为其增加显示额度: {amount}\n该用户当前总显示额度为: {total}",
        "adjust.success_dec": "✅ 操作成功！\n--------------------\n目标用户ID: {site_id}\n已为其减少显示额度: {amount}\n该用户当前总显示额度为: {total}",
        "adjust.not_found": "❌ 操作失败：未在绑定记录中找到与 {id} 相关的用户。",
        "adjust.fetch_failed": "❌ 操作失败：无法从网站获取ID为 {site_id} 的用户信息。",
        "adjust.update_failed": "❌ 操作失败：向网站更新ID为 {site_id} 的余额时发生错误。",
        # 打劫
        "heist.no_target": "🤔 打劫谁呢？请 @ 你要打劫的目标。",
        "heist.too_many": "🏃‍♂️ 不要太贪心，一次只能打劫一个目标！",
        "heist.api_error": "- 发生了一个API错误，请联系管理员。",
        "heist.unknown": "❓ 发生未知错误。",
        # 榜单
        "leaderboard.disabled": "排行榜功能未开启。",
        "leaderboard.header": "🏆 群余额榜 TOP {top_n}\n{balance}\n\n🥷 打劫榜 TOP {top_n}\n{heist}",
        "leaderboard.no_balance_cache": "暂无缓存余额数据（请先执行签到或查余额）。",
        "leaderboard.no_balance": "暂无缓存余额数据。",
        "leaderboard.no_heist": "暂无打劫记录。",
        "leaderboard.heist_line": "{prefix} QQ:{qq} → 出手{attempts}次 胜{wins}次 净赚{net}",
        # 消耗榜
        "consumption.fetching": "🔄 正在拉取全站近 {hours} 小时消耗日志，请稍候…",
        "consumption.disabled": "消耗排行榜功能未开启。",
        "consumption.fetch_failed": "❌ 拉取全站消耗日志失败，请稍后再试或联系管理员。",
        "consumption.no_data": "近 {hours} 小时暂无消耗记录。",
        "consumption.header": "📊 全站消耗榜 TOP {top_n}（近 {hours} 小时）\n{lines}",
        "consumption.line": "{prefix} {username} → {tokens} tokens",
        "consumption.line_bound": "{prefix} {username} [QQ:{qq}] → {tokens} tokens",
        "consumption.line_quota": "{prefix} {username} → {tokens} tokens · 消耗 {quota} 额度",
        "consumption.line_bound_quota": "{prefix} {username} [QQ:{qq}] → {tokens} tokens · 消耗 {quota} 额度",
        # 退群公告
        "leave.announcement": "成员【{nickname}】({qq}) 已主动退出群聊。\n其绑定的网站数据已自动解绑，用户组已重置。",
        "kick.announcement": "成员【{nickname}】({qq}) 已被管理员【{op}】移出群聊。\n其绑定的网站数据已自动解绑，用户组已重置。",
        # 数据库导入导出
        "db_transfer.usage": "用法：发送「new-tx 导出」把当前数据库内容导出到迁移文件，或「new-tx 导入」把迁移文件导入当前数据库。\n⚠️ 导入为覆盖操作：当前库中绑定 / 签到状态 / 打劫日志表会先清空再写入文件内容。",
        "db_transfer.act_export": "导出",
        "db_transfer.act_import": "导入",
        "db_transfer.working": "🔄 正在{action}数据库，请稍候…",
        "db_transfer.detail_line": "{label}：{count} 条",
        "db_transfer.export_done": "✅ 导出完成，已写入迁移文件 transfer.db：\n{detail}\n共 {total} 条记录。",
        "db_transfer.import_done": "✅ 导入完成，当前数据库已更新：\n{detail}\n共 {total} 条记录。",
        "db_transfer.no_file": "❌ 未找到迁移文件（transfer.db），请先执行「new-tx 导出」。",
        "db_transfer.empty_file": "❌ 迁移文件（transfer.db）中没有任何数据，已拒绝导入以保护当前数据库。\n如确认要导入，请先检查该文件是否正确。",
        "db_transfer.failed": "❌ 数据库{action}失败：{err}\n请查看后台日志。",
        "db_transfer.label_bindings": "QQ 绑定",
        "db_transfer.label_openid_bindings": "OpenID 绑定",
        "db_transfer.label_check_in_state": "签到状态",
        "db_transfer.label_heist_log": "打劫日志",
        "db_transfer.label_pk": "PK 记录",
        # 红包
        "rp.disabled": "🧧 红包功能当前未开启。",
        "rp.count_required": "请提供红包份数。\n用法：发红包 [份数] [总额度]，例如：发红包 10 50",
        "rp.count_invalid": "❌ 红包份数应为正整数，你输入的「{input}」无法识别。\n例如：发红包 10 50",
        "rp.count_too_large": "❌ 单个红包最多 {max} 份。",
        "rp.amount_required": "请提供红包总额度。\n用法：发红包 [份数] [总额度]，例如：发红包 10 50",
        "rp.amount_invalid": "❌ 总额度应为正数，你输入的「{input}」无法识别。\n例如：发红包 10 50",
        "rp.amount_too_large": "❌ 单个红包总额度不能超过 {max}。",
        "rp.too_small": "❌ 总额度太低，无法拆成 {count} 份，请提高额度或减少份数。",
        "rp.created": "🧧 {creator} 发出了一个拼手气红包！\n💰 总额度 {amount} · 共 {count} 份\n👉 快发送「抢红包 {pid}」来抢！\n⏰ {hours} 小时内有效，先到先得～",
        "rp.official_only_hint": "\n🤖 本红包仅可通过官方机器人抢夺，在其他机器人处发送无效～",
        "rp.pid_required": "请提供红包编号（发红包时会公布），例如：抢红包 k3m9ab",
        "rp.pid_invalid": "❌ 红包编号应为字母或数字组合，你输入的「{input}」无法识别。\n例如：抢红包 k3m9ab 或 抢红包 3",
        "rp.not_found": "❌ 没有找到红包 #{pid}，检查一下编号吧。",
        "rp.expired": "⏰ 手慢了，这个红包已过期。",
        "rp.empty": "😭 手慢了，红包已被抢完！",
        "rp.already": "🙃 你已经抢过这个红包啦，别贪心～",
        "rp.api_error": "- 红包入账失败（本次未消耗份额），请稍后再试或联系管理员。",
        "rp.success": "🎉 {grabber} 抢到了 {amount} 额度！\n💰 已直接存入余额\n📦 本红包剩余 {remain}/{total} 份",
        "rp.rank.header": "🎊 本红包已被抢完！手气排行（金额从多到少）：",
        "rp.rank.line": "{rank}{name} · {amount}",
        "rp.rank.more": "…另有 {n} 人未展示",
        # 个人红包（普通用户，扣自己余额）
        "rp.user.disabled": "🧧 个人红包功能当前未开启。",
        "rp.user.count_required": "请提供红包份数。\n用法：个人红包 [份数] [总额度]，例如：个人红包 10 50",
        "rp.user.count_invalid": "❌ 红包份数应为正整数，你输入的「{input}」无法识别。\n例如：个人红包 10 50",
        "rp.user.count_too_large": "❌ 单个红包最多 {max} 份。",
        "rp.user.amount_required": "请提供红包总额度。\n用法：个人红包 [份数] [总额度]，例如：个人红包 10 50",
        "rp.user.amount_invalid": "❌ 总额度应为正数，你输入的「{input}」无法识别。\n例如：个人红包 10 50",
        "rp.user.not_verified": "🔒 首次发个人红包需要先验证身份。\n请发送：验证令牌 你的网站访问令牌\n（在 New API 网站「个人设置 → 生成系统访问令牌」获取）\n⚠️ 令牌等同密码：建议私聊机器人验证；若已在群内发送，验证后请尽快到网站重置令牌。",
        "rp.user.balance_unavailable": "❌ 暂时无法获取你的账户余额，请稍后再试。",
        "rp.user.limit_reached": "🧧 今日个人红包次数已用完～\n💰 当前余额 {balance}（每满 {per} 可发 1 次，每日上限 {max} 次）\n📅 今日已发 {used} 次，明天再来吧！",
        "rp.user.exceeds_balance": "❌ 红包总额度不能超过你的余额（当前 {balance}）。",
        "rp.user.deduct_failed": "❌ 扣款失败，红包未发出。请稍后再试或联系管理员。",
        "rp.user.create_failed": "❌ 红包创建失败，已为你退还 {amount} 额度。请稍后再试；若额度未到账请联系管理员。",
        "rp.user.created": "🧧 {creator} 用自己的额度发出了拼手气红包！\n💰 总额度 {amount} · 共 {count} 份（已从余额扣除）\n📦 发送者余额剩余 {balance}\n👉 快发送「抢红包 {pid}」来抢！\n⏰ {hours} 小时内有效 · 发送者今日还可发 {left} 次",
        # 红包诊断（管理员）
        "rp.diag.header": "🧧 红包诊断\n运行版本: {version}\n仅官机开关: {toggle}\n官机Markdown: {md}\n你的身份: {identity}\n判定规则: 触发者数字QQ=野机(拦截) · OpenID=官机(放行)\n—— 最近红包指令（≤10条） ——\n{records}",
        "rp.diag.toggle_on": "✅ 已开启",
        "rp.diag.toggle_off": "❌ 未开启",
        "rp.diag.identity_wild": "QQ {sender}（野机）",
        "rp.diag.identity_official": "{sender}（官机 OpenID）",
        "rp.diag.kind_wild": "野机",
        "rp.diag.kind_official": "官机",
        "rp.diag.verdict_block": "已拦截(静默)",
        "rp.diag.verdict_pass": "放行",
        "rp.diag.record": "· {time} {cmd}｜触发者={sender}（{kind}）→ {verdict}",
        "rp.diag.no_records": "暂无记录。若刚在群里发过发/抢红包却看不到记录，说明指令没有到达本插件实例——请检查是否运行了旧版本或重复副本。",
        # 个人红包身份验证
        "rp.verify.token_required": "用法：验证令牌 [你的网站访问令牌]\n在 New API 网站「个人设置 → 生成系统访问令牌」获取。\n⚠️ 令牌等同密码：建议私聊机器人验证；若已在群内发送，验证后请尽快到网站重置令牌。",
        "rp.verify.success": "✅ 身份验证成功！令牌有效且与网站ID {site_id} 对应。\n之后即可直接使用「个人红包 [份数] [总额度]」，无需重复验证。",
        "rp.verify.failed": "❌ 验证失败：访问令牌无效，或与你绑定的网站ID不匹配。\n请确认令牌来自你绑定的账号后重新发送。",
        # PK
        "pk.disabled": "⚔️ PK 功能当前未开启。",
        "pk.usage": "用法：PK 对方网站ID 金额，例如：PK 13 100（也可 @对方 100）。\n发起后立即扣除你的押注；对方需在 5 分钟内发送「接受PK 你的网站ID」应战并扣除同等押注，超时自动退还。",
        "pk.amount_invalid": "❌ 押注金额必须是大于 0 的有效数字。",
        "pk.stake_too_large": "❌ 单次下注不能超过 {max} 额度。",
        "pk.max_hint": "⚠️ 单次下注上限：{max} 额度（超过将被拒绝）。",
        "pk.rank.no_data": "🤔 今天还没有已结算的 PK。",
        "pk.rank.summary": "🏆 今日 PK 盈亏榜（{date}）\n已结算 {count} 局｜押注总流动 {total} 额度｜参与玩家 {players} 人",
        "pk.rank.line": "{medal}网站ID {site}｜局数 {games}｜胜 {wins} 负 {losses}｜净 {net}",
        "pk.rank.profit_header": "💰 盈利 TOP{n}",
        "pk.rank.loss_header": "💸 亏损 TOP{n}",
        "pk.history.id_invalid": "用法：PK战绩（查自己）或 PK战绩 网站ID（管理员可查他人）。",
        "pk.history.admin_required": "❌ 只有管理员可以查询其他用户的 PK 数据。",
        "pk.history.header": "📊 你的今日 PK 战绩（{date}）\n网站ID {site}",
        "pk.history.header_all": "📊 你的 PK 总战绩（自 PK 功能上线以来）\n网站ID {site}",
        "pk.history.stats": "局数 {games}｜胜 {wins} 负 {losses}｜净 {net}\n赢局入账 {win}｜输局支出 {lose}\n作为挑战者 {chal} 局｜作为应战者 {opp} 局\n—— 最近对局（≤5）——",
        "pk.history.line": "{time}｜对 网站ID {other}｜押 {stake}｜{result}｜尾数 {digit}",
        "pk.history.win": "✅ 胜",
        "pk.history.lose": "❌ 负",
        "pk.history.no_data": "🤔 你今天还没有 PK 记录。",
        # 醍醐测智
        "tihudace.disabled": "❌ 醍醐测智未配置：codex_settings.prompt_template 为空。",
        "tihudace.working": "🎨 已召唤 {model}，正在绘制 SVG 动画（使用配置提示词），请稍候…",
        "tihudace.model_timeout": "❌ 模型生成超时，本次没有生成图片。请稍后再试；定时任务会在下个周期继续。",
        "tihudace.model_failed": "❌ 模型请求失败或返回空结果，本次未生成图片；请检查模型服务和插件日志。",
        "tihudace.run.status": "本次启动后的最近任务：{state}｜耗时：{elapsed} 秒",
        "tihudace.run.IDLE": "尚未触发",
        "tihudace.run.GENERATING": "正在生成图片",
        "tihudace.run.SENDING": "正在推送",
        "tihudace.run.OK": "各渠道发送调用成功（不代表客户端显示）",
        "tihudace.run.MODEL_TIMEOUT": "模型生成超时，下个周期继续",
        "tihudace.run.MODEL_FAILED": "模型请求失败或空结果",
        "tihudace.run.HTML_MISSING": "模型内容不是 HTML/SVG",
        "tihudace.run.RENDER_FAILED": "图片渲染失败",
        "tihudace.run.DISABLED": "生成提示词未配置",
        "tihudace.run.PUSH_FAILED": "部分或全部推送失败，请查日志",
        "tihudace.run.NO_TARGETS": "图片已生成，但未配置推送目标",
        "tihudace.run.CANCELLED": "任务已取消",
        "tihudace.run.INTERNAL_ERROR": "任务发生异常，请查日志",
        "tihudace.html_missing": "❌ 模型没有返回可渲染的 HTML/SVG 内容。",
        "tihudace.render_failed": "❌ 图片渲染失败（容器可能缺少 Chromium 或渲染超时）。",
        # 批量测智（多站点）
        "tihudace.batch.working": "🎨 批量测智：正在向 {count} 个站点请求并逐个渲染，请稍候…",
        "tihudace.batch.header": "🧪 批量测智结果：{ok}/{total} 个站点出图成功",
        "tihudace.batch.item": "✅ {name}｜{model}：{state}",
        "tihudace.batch.state.ok": "已出图",
        "tihudace.batch.item_failed": "❌ {name}｜{model}：{reason}",
        "tihudace.batch.none": "本次没有站点成功出图；请检查站点地址、密钥、模型名与插件日志。",
        "tihudace.batch.issue_header": "⚠️ 以下站点配置无效，已跳过（密钥已脱敏）：",
        "tihudace.batch.issue_line": "第 {index} 条（{preview}）：{reason}",
        "tihudace.batch.issue_more": "另有 {count} 条无效配置未列出。",
        "tihudace.spec.reason.invalid_format": "格式错误，应为 站点地址|APIKey|模型名 或 名称|站点地址|APIKey|模型名",
        "tihudace.spec.reason.invalid_base_url": "站点地址无效，需为 http(s) 地址",
        "tihudace.spec.reason.missing_model": "缺少模型名",
        "tihudace.spec.reason.missing_api_key": "缺少 APIKey（条目内和 api_key 均为空）",
        "tihudace.spec.reason.too_many_targets": "超出单次批量上限（最多 10 个站点）",
        "tihudace.push.group_only": "请在 QQ 官方普通群或 OneBot 群中执行此指令；不支持私聊或 QQ 频道。",
        "tihudace.push.admin_only": "此操作需要 AstrBot 管理员权限（官方机器人使用发送者 OpenID 判断）。",
        "tihudace.push.bad_number": "请输入有效群号：醍醐推送绑定 群号 / 醍醐推送解绑 群号。OneBot 绑定的群号必须与当前群一致。",
        "tihudace.push.bad_store": "群绑定数据格式异常，已停止修改，请联系管理员检查。",
        "tihudace.push.session": "当前 QQ 群推送会话（可直接填入插件的 QQ 推送群号列表）：\n{session}\n也可在列表填写普通群号，再由 AstrBot 管理员在本群执行：醍醐推送绑定 群号。",
        "tihudace.push.conflict": "群号别名 {group} 已绑定其他会话；未覆盖。需要更换时先执行：醍醐推送解绑 {group}，再到正确群中绑定。",
        "tihudace.push.bound": "✅ 已将群号别名 {group} 绑定到当前 QQ 群。\n{state}\n官方接口无法校验普通群号，请确认此群确实对应填写的群号；绑定本身不会立即推送。",
        "tihudace.push.selected": "该群号已在推送列表中，将在定时任务运行时尝试推送。",
        "tihudace.push.not_selected": "该群号尚未在推送列表中；请在插件配置 → 醍醐测智定时推送 → QQ 推送群号列表中添加。",
        "tihudace.push.unbound": "已解除群号别名 {group} 的绑定。要停止推送，请同时从 QQ 推送群号列表移除该群号及其直接会话项。",
        "tihudace.push.status": "醍醐测智 QQ 推送状态\n定时开关：{enabled}｜间隔：{interval} 分钟\n配置目标：{count}｜可路由群：{ready}（不代表已送达）",
        "tihudace.push.issue": "目标 {target} 未就绪：{reason}",
        "tihudace.push.reason.invalid_configuration": "配置项格式错误，请填写群号或完整群会话。",
        "tihudace.push.reason.platform_unavailable": "对应机器人实例不存在或未启用。",
        "tihudace.push.reason.invalid_binding": "群绑定数据无效，请解绑后重新绑定。",
        "tihudace.push.reason.unbound_group_number": "群号尚未绑定；请在对应群执行：醍醐推送绑定 群号。",
        "tihudace.push.reason.invalid_group_id": "群标识格式与对应平台不匹配。",
        "pk.self": "🤦 不能挑战自己。",
        "pk.target_not_found": "❌ 未找到目标 {id} 的绑定记录。",
        "pk.already_pending": "⏳ 你与该目标已有一局待应战的 PK，请等待对方应战或 5 分钟过期。",
        "pk.insufficient_balance": "❌ 余额不足，无法进行 PK：需要 {need} 额度，当前 {balance}。",
        "pk.deduct_failed": "❌ 押注扣款失败，未发起挑战。请稍后再试或联系管理员。",
        "pk.db_failed": "❌ 创建 PK 记录失败，押金已退还。",
        "pk.created": "⚔️ 网站ID {challenger} 向 网站ID {opponent} 发起 PK！\n💰 双方各押 {amount} 额度（发起方已扣款）\n👉 请在 {minutes} 分钟内发送「接受PK {challenger}」应战\n⏰ 超时未应战将自动结束并退还押金。",
        "pk.accept.not_found": "🤔 没有找到等待你应战的 PK（可能已过期自动结束，或挑战不存在）。",
        "pk.accept.multiple": "🤔 有 {count} 局 PK 在等你应战，请指定发起人：接受PK 网站ID",
        "pk.accept.expired": "⏰ 这局 PK 已超过保留时间，自动结束并退还了发起人押金。",
        "pk.accept.insufficient": "❌ 余额不足，无法应战 PK：需要 {need} 额度，当前 {balance}。",
        "pk.accept.deduct_failed": "❌ 应战押注扣款失败，请稍后再试。",
        "pk.settled": "⚔️ PK 结算！\n🕐 结算毫秒时间戳：{time}\n🧮 计算过程：{expression} = {digit_sum} → 尾数 {digit}（{parity}）\n🏆 胜者：网站ID {winner}（获得 {pot} 额度）\n💸 败者：网站ID {loser}\n💰 结算后余额：\n  网站ID {winner}（胜）→ {winner_balance}\n  网站ID {loser}（负）→ {loser_balance}\n📜 规则：时间戳全部数字求和取尾数，双数=挑战者胜，奇数=应战者胜。",
        "pk.parity_odd": "单数",
        "pk.parity_even": "双数",
        "pk.balance_unavailable": "查询失败",
        "pk.settle_failed": "❌ PK 结算出现异常，已尝试原路退还双方押金；请稍后用「查询余额」核对，仍有问题请联系管理员。",
        "pk.auto_failed": "⚔️ 已自动应战但未能完成结算（管理员余额不足或扣款失败），你的押注已退还。",
    },
    "en": {
        "common.at_or_id_required": "Please @ a user or enter a website ID / QQ number.",
        "common.amount_required": "Please provide the quota amount.",
        "common.unknown": "Unknown",
        "common.unexpected_error": "❌ An unexpected error occurred while processing the command: {err}",
        "not_bound": "You haven't bound a website ID yet.\nPlease send the command \"绑定 [your website ID]\" to bind first.",
        "ping.connected": "✅ Connected",
        "ping.disconnected": "❌ Failed",
        "ping.running": "🎉 Pong! NewAPI plugin suite V{version} is running!",
        "ping.db_engine": "Database Engine",
        "ping.db_status": "Database Status",
        "ping.api_status": "New API Status",
        # Command reference
        "help.header": "📚 Transit Station Command Reference (NewAPI suite V{version})\n====================\n",
        "help.body": (
            "[Account]\n"
            "绑定 <website ID> — bind your website account\n"
            "查询余额 — check your own balance\n"
            "签到 — daily check-in quota\n"
            "查余额 <website ID> (admin) — others' balance\n"
            "查询 <website ID/QQ/OpenID> (admin) — bindings\n"
            "查ID <username/email> — find website user ID\n"
            "调整余额 <website ID> <amount> (admin) — add/subtract\n"
            "解绑 <website ID> (admin)\n"
            "\n[Fun]\n"
            "打劫 <website ID> or @member — heist\n"
            "PK <website ID> <amount> — challenge (accept within 5 min; auto-accepted vs admin)\n"
            "接受PK [challenger ID] — sum timestamp digits; even tail = challenger wins\n"
            "PK榜 — daily PK P&L leaderboard\n"
            "PK战绩 [website ID] — PK data (others: admin only)\n"
            "PK总战绩 [website ID] — all-time PK data (others: admin only)\n"
            "醍醐测智 — generate an SVG screenshot with the configured model; scheduled pushes are configured in plugin settings; signed DingTalk robots require the SEC secret\n"
            "醍醐测智 (multi-site batch) — under plugin settings → Codex generation → multi-site list, add one site per line as url|api_key|model or name|url|api_key|model;\n"
            "　　one command batch-tests every site: each result is rendered and sent separately, a failed site does not stop the others; up to 10 sites, invalid lines are skipped and reported with keys masked; an empty list falls back to single-site mode\n"
            "DingTalk images — enable MinIO/S3 in image storage settings; PNG uploads are checked for public access before embedding; failures are reported with a text-only fallback\n"
            "Image storage diagnostics — logs contain only safe error codes; storage_full means insufficient storage space: ask an administrator to clean up or expand storage; no automatic retries or relaxed public-access checks\n"
            "Scheduled QQ pushes — add multiple groups to the QQ push group list in plugin settings; an empty list stops QQ pushes; image captions include generation time (Beijing time, UTC+8, shared across groups in the same run)\n"
            "醍醐推送会话 — get a direct push session in the intended group\n"
            "醍醐推送绑定 <group number> (AstrBot admin) — bind the numeric alias to this official group session; binding does not subscribe\n"
            "醍醐推送解绑 <group number> / 醍醐推送状态 (AstrBot admin) — remove a binding or inspect targets; official proactive-message permissions and quotas still apply\n"
            "醍醐推送状态 also shows the latest run stage, failure reason and elapsed time; model requests wait up to 10 minutes by default, then continue next cycle after a timeout\n"
            "榜单 — balance & heist leaderboards\n"
            "消耗榜 — site-wide token consumption\n"
            "\n[Red packets]\n"
            "发红包 <count> <total> (admin)\n"
            "抢红包 <code>\n"
            "个人红包 <count> <total> — from your balance\n"
            "验证令牌 <access token> — required before personal packets\n"
            "红包诊断 (admin)\n"
            "\n[Data & diagnostics]\n"
            "new-tx 导出 / new-tx 导入 (admin; import overwrites)\n"
            "pingapi — DB and New API status\n"
            "提及诊断 — read-only QQ mention check\n"
            "\n[Model health (separate plugin)]\n"
            "模型情况 — health cards\n"
            "模型情况 <theme> / 模型情况主题\n"
            "模型情况会话 (admin)\n"
            "\nNo @mention or slash needed: send the command directly."
        ),
        "query_balance.failed": "Query failed: unable to fetch your balance from the website. Please try again later or contact the admin.",
        "query_balance.success": "Query successful!\n--------------------\nYour bound website ID: {site_id}\nCurrent remaining quota: {quota}",
        "query_other.not_found": "❌ Query failed: no binding record found for {id}.",
        "query_other.failed": "Query failed: unable to fetch this user's balance. Please try again later or contact the admin.",
        "query_other.label_website": "Website ID",
        "query_other.label_qq": "QQ",
        "query_other.success": "✅ Query successful!\n--------------------\nInput type: {label}\n{label}: {id}\nBound website ID: {site_id}\nCurrent remaining quota: {quota}",
        "bind.id_required": "Please provide your website ID.\nUsage: 绑定 [your website ID] (digits only), e.g. 绑定 1234",
        "bind.id_invalid": "❌ The website ID must be numeric, but \"{input}\" cannot be recognized.\nPlease check and retry, e.g. 绑定 1234",
        "bind.validating": "Validation passed, binding...",
        "bind.already_bound": "Your QQ is already bound to website ID {site_id}, no need to bind again.",
        "bind.qq_level_low": "Sorry, your QQ level ({level}) is below the required {min_level}; unable to bind.",
        "bind.api_user_not_found": "Check failed: no user with ID {site_id} exists on the website. Please check your ID.",
        "bind.website_blacklisted": "Check failed: website ID {site_id} has been blacklisted; unable to bind.",
        "bind.user_blacklisted": "Check failed: your QQ {qq} has been blacklisted; unable to bind.",
        "bind.id_taken": "Check failed: ID {site_id} is already bound to another user.",
        "bind.success": "Congratulations! Binding successful!\nYour QQ is now bound to website ID {site_id}.\nYou've been promoted to group 【{group}】.",
        "bind.openid_success": "Congratulations! Binding successful!\nYour OpenID {openid} is now bound to website ID {site_id}.\nYou've been promoted to group 【{group}】.",
        "bind.openid_success_no_group": "Congratulations! Binding successful!\nYour OpenID {openid} is now bound to website ID {site_id}.\nℹ️ \"Wild-bind-only group change\" is enabled: official (OpenID) binding does not change the user group.",
        "bind.openid_need_qq": "❌ Binding failed: \"official binding requires an existing QQ binding\" is enabled, and website ID {site_id} has no QQ (wild bot) binding yet.\nPlease run 绑定 {site_id} via the wild bot first.",
        "bind.failed": "An unknown error occurred during binding; the operation was rolled back. Please contact the admin.",
        "check_in.disabled": "Sorry, daily check-in is currently disabled.",
        "check_in.already": "You've already checked in today, come back tomorrow!",
        "check_in.api_user_not_found": "Check-in failed: unable to fetch your website user info. Please contact the admin.",
        "check_in.api_update_failed": "Check-in failed: error updating quota on the server. Please try again later.",
        "check_in.unknown": "An unknown error occurred during check-in. Please contact the admin.",
        "unbind.success": "✅ Success!\nWebsite ID {site_id} has been unbound from QQ user {qq}.",
        "unbind.not_found": "❌ Invalid: no binding record found for website ID {site_id}.",
        "unbind.failed": "❌ Failed: an unknown error occurred while purging website ID {site_id}. Please check the logs.",
        "lookup.website": "✅ Found! Input was a [Website ID]\n--------------------\nWebsite ID: {site_id}\nBound to QQ: {qq}\nBound at: {time}",
        "lookup.qq": "✅ Found! Input was a [QQ]\n--------------------\nQQ: {qq}\nBound to website ID: {site_id}\nBound at: {time}",
        "lookup.not_found": "❌ Query failed: no binding record found for {id}.",
        "lookup.openid_extra": "\n🔗 Also bound via OpenID (official bot): {openid}",
        # Search by username/email (available to everyone; email masked)
        "search.usage": "Usage: 查ID <username or email>\ne.g. 查ID zhangsan or 查ID zhangsan@example.com",
        "search.header": "🔍 Users matching \"{keyword}\" (up to 5):",
        "search.line": "ID: {user_id} | username: {username} | email: {email}",
        "search.not_found": "❌ No user matches \"{keyword}\".",
        "search.failed": "❌ User search API failed. Please retry later or contact the admin.",
        "adjust.success_inc": "✅ Success!\n--------------------\nTarget user ID: {site_id}\nIncreased display quota by: {amount}\nCurrent total display quota: {total}",
        "adjust.success_dec": "✅ Success!\n--------------------\nTarget user ID: {site_id}\nDecreased display quota by: {amount}\nCurrent total display quota: {total}",
        "adjust.not_found": "❌ Failed: no user found for {id}.",
        "adjust.fetch_failed": "❌ Failed: unable to fetch info for user {site_id}.",
        "adjust.update_failed": "❌ Failed: error updating balance for user {site_id}.",
        "heist.no_target": "🤔 Who to rob? Please @ your target.",
        "heist.too_many": "🏃‍♂️ Don't be greedy, only one target at a time!",
        "heist.api_error": "- An API error occurred, please contact the admin.",
        "heist.unknown": "❓ An unknown error occurred.",
        "leaderboard.disabled": "Leaderboard is disabled.",
        "leaderboard.header": "🏆 Group Balance TOP {top_n}\n{balance}\n\n🥷 Heist TOP {top_n}\n{heist}",
        "leaderboard.no_balance_cache": "No cached balance data yet (check in or query balance first).",
        "leaderboard.no_balance": "No cached balance data.",
        "leaderboard.no_heist": "No heist records yet.",
        "leaderboard.heist_line": "{prefix} QQ:{qq} → {attempts} attempts, {wins} wins, net {net}",
        "consumption.fetching": "🔄 Fetching site-wide consumption logs for the last {hours} hours, please wait…",
        "consumption.disabled": "Consumption leaderboard is disabled.",
        "consumption.fetch_failed": "❌ Failed to fetch consumption logs. Please try again later or contact the admin.",
        "consumption.no_data": "No consumption records in the last {hours} hours.",
        "consumption.header": "📊 Site-wide Consumption TOP {top_n} (last {hours}h)\n{lines}",
        "consumption.line": "{prefix} {username} → {tokens} tokens",
        "consumption.line_bound": "{prefix} {username} [QQ:{qq}] → {tokens} tokens",
        "consumption.line_quota": "{prefix} {username} → {tokens} tokens · {quota} quota",
        "consumption.line_bound_quota": "{prefix} {username} [QQ:{qq}] → {tokens} tokens · {quota} quota",
        "leave.announcement": "Member 【{nickname}】({qq}) left the group.\nTheir binding has been removed and user group reset.",
        "kick.announcement": "Member 【{nickname}】({qq}) was removed by admin 【{op}】.\nTheir binding has been removed and user group reset.",
        # Database import/export
        "db_transfer.usage": "Usage: send \"new-tx 导出\" to export the current database into the transfer file, or \"new-tx 导入\" to import the transfer file into the current database.\n⚠️ Import is destructive: bindings / check-in states / heist logs in the current database will be cleared and replaced by the file contents.",
        "db_transfer.act_export": "export",
        "db_transfer.act_import": "import",
        "db_transfer.working": "🔄 Database {action} in progress, please wait…",
        "db_transfer.detail_line": "{label}: {count}",
        "db_transfer.export_done": "✅ Export finished, written to transfer file transfer.db:\n{detail}\nTotal {total} records.",
        "db_transfer.import_done": "✅ Import finished, current database updated:\n{detail}\nTotal {total} records.",
        "db_transfer.no_file": "❌ Transfer file (transfer.db) not found. Please run \"new-tx 导出\" first.",
        "db_transfer.empty_file": "❌ The transfer file (transfer.db) contains no data; import was refused to protect the current database.",
        "db_transfer.failed": "❌ Database {action} failed: {err}\nPlease check the logs.",
        "db_transfer.label_bindings": "QQ bindings",
        "db_transfer.label_openid_bindings": "OpenID bindings",
        "db_transfer.label_check_in_state": "Check-in states",
        "db_transfer.label_heist_log": "Heist logs",
        "db_transfer.label_pk": "PK records",
        # Red packets
        "rp.disabled": "🧧 The red packet feature is currently disabled.",
        "rp.count_required": "Please provide the number of shares.\nUsage: 发红包 [shares] [total quota], e.g. 发红包 10 50",
        "rp.count_invalid": "❌ Shares must be a positive integer, but \"{input}\" cannot be recognized.\ne.g. 发红包 10 50",
        "rp.count_too_large": "❌ A packet can have at most {max} shares.",
        "rp.amount_required": "Please provide the total quota.\nUsage: 发红包 [shares] [total quota], e.g. 发红包 10 50",
        "rp.amount_invalid": "❌ Total quota must be a positive number, but \"{input}\" cannot be recognized.\ne.g. 发红包 10 50",
        "rp.amount_too_large": "❌ Total quota per packet cannot exceed {max}.",
        "rp.too_small": "❌ Total quota is too low to split into {count} shares. Increase the amount or reduce the share count.",
        "rp.created": "🧧 {creator} sent a lucky red packet!\n💰 Total {amount} quota · {count} shares\n👉 Send \"抢红包 {pid}\" to grab!\n⏰ Valid for {hours} hours, first come first served~",
        "rp.official_only_hint": "\n🤖 This packet can only be grabbed via the official bot; grabs sent elsewhere are ignored.",
        "rp.pid_required": "Please provide the packet code (announced when created), e.g. 抢红包 k3m9ab",
        "rp.pid_invalid": "❌ Packet code must be letters/digits, but \"{input}\" cannot be recognized.\ne.g. 抢红包 k3m9ab or 抢红包 3",
        "rp.not_found": "❌ Packet #{pid} not found. Please check the ID.",
        "rp.expired": "⏰ Too slow, this red packet has expired.",
        "rp.empty": "😭 Too slow, the red packet has been fully grabbed!",
        "rp.already": "🙃 You've already grabbed this packet, don't be greedy~",
        "rp.api_error": "- Failed to credit the red packet (share not consumed). Please retry later or contact the admin.",
        "rp.success": "🎉 {grabber} grabbed {amount} quota!\n💰 Credited directly to your balance\n📦 {remain}/{total} shares left in this packet",
        "rp.rank.header": "🎊 This packet has been fully grabbed! Ranking (largest first):",
        "rp.rank.line": "{rank}{name} · {amount}",
        "rp.rank.more": "…and {n} more grabbers not shown",
        # User red packets (deducted from sender balance)
        "rp.user.disabled": "🧧 The personal red packet feature is currently disabled.",
        "rp.user.count_required": "Please provide the number of shares.\nUsage: 个人红包 [shares] [total], e.g. 个人红包 10 50",
        "rp.user.count_invalid": "❌ Shares must be a positive integer, but \"{input}\" cannot be recognized.\ne.g. 个人红包 10 50",
        "rp.user.count_too_large": "❌ A packet can have at most {max} shares.",
        "rp.user.amount_required": "Please provide the total quota.\nUsage: 个人红包 [shares] [total], e.g. 个人红包 10 50",
        "rp.user.amount_invalid": "❌ Total quota must be a positive number, but \"{input}\" cannot be recognized.\ne.g. 个人红包 10 50",
        "rp.user.not_verified": "🔒 Identity verification is required before sending your first personal red packet.\nPlease send: 验证令牌 your-website-access-token\n(Get it on the New API site under Profile → Generate system access token)\n⚠️ Treat the token like a password: verify in a private chat if possible; reset it on the site if it was exposed in a group.",
        "rp.user.balance_unavailable": "❌ Unable to fetch your account balance right now. Please try again later.",
        "rp.user.limit_reached": "🧧 You've used up today's personal red packet sends~\n💰 Current balance {balance} (1 send per {per} of balance, up to {max}/day)\n📅 Sent today: {used}. Come back tomorrow!",
        "rp.user.exceeds_balance": "❌ The packet total cannot exceed your balance (currently {balance}).",
        "rp.user.deduct_failed": "❌ Deduction failed; the packet was not created. Please retry later or contact the admin.",
        "rp.user.create_failed": "❌ Packet creation failed; {amount} has been refunded. Please retry later; contact the admin if the refund is missing.",
        "rp.user.created": "🧧 {creator} sent a lucky red packet from their own balance!\n💰 Total {amount} · {count} shares (deducted from balance)\n📦 Sender's remaining balance: {balance}\n👉 Send \"抢红包 {pid}\" to grab!\n⏰ Valid for {hours} hours · sender can send {left} more today",
        # Red packet diagnostics (admin)
        "rp.diag.header": "🧧 Red packet diagnostics\nRunning version: {version}\nOfficial-only toggle: {toggle}\nOfficial markdown: {md}\nYour identity: {identity}\nRule: numeric QQ = wild bot (blocked) · OpenID = official bot (allowed)\n— Recent red packet commands (≤10) —\n{records}",
        "rp.diag.toggle_on": "✅ ON",
        "rp.diag.toggle_off": "❌ OFF",
        "rp.diag.identity_wild": "QQ {sender} (wild bot)",
        "rp.diag.identity_official": "{sender} (official OpenID)",
        "rp.diag.kind_wild": "wild",
        "rp.diag.kind_official": "official",
        "rp.diag.verdict_block": "BLOCKED(silent)",
        "rp.diag.verdict_pass": "allowed",
        "rp.diag.record": "· {time} {cmd} | sender={sender} ({kind}) → {verdict}",
        "rp.diag.no_records": "No records yet. If you just sent a red packet command in the group but see nothing here, the command never reached this plugin instance — check for an outdated version or a duplicate copy.",
        # Personal packet identity verification
        "rp.verify.token_required": "Usage: 验证令牌 [your website access token]\nGet it on the New API site under Profile → Generate system access token.\n⚠️ Treat the token like a password: verify in private chat if possible; reset it if exposed in a group.",
        "rp.verify.success": "✅ Verification succeeded! The token is valid and matches website ID {site_id}.\nYou can now use \"个人红包 [shares] [total]\" directly — no need to verify again.",
        "rp.verify.failed": "❌ Verification failed: the access token is invalid or does not match your bound website ID.\nMake sure the token belongs to your bound account and try again.",
        # PK
        "pk.disabled": "⚔️ The PK feature is currently disabled.",
        "pk.usage": "Usage: PK <opponent website ID> <amount>, e.g. PK 13 100 (or mention the opponent then amount).\nYour stake is deducted immediately; the opponent has 5 minutes to send \"接受PK <your website ID>\", otherwise the stake is refunded automatically.",
        "pk.amount_invalid": "❌ The stake must be a positive finite number.",
        "pk.stake_too_large": "❌ A single stake cannot exceed {max} quota.",
        "pk.max_hint": "⚠️ Max stake per PK: {max} quota (larger stakes are rejected).",
        "pk.rank.no_data": "🤔 No settled PK today yet.",
        "pk.rank.summary": "🏆 Daily PK P&L ({date})\nSettled {count} games | total stake flow {total} | {players} players",
        "pk.rank.line": "{medal}Website ID {site} | games {games} | W {wins} L {losses} | net {net}",
        "pk.rank.profit_header": "💰 Profit TOP{n}",
        "pk.rank.loss_header": "💸 Loss TOP{n}",
        "pk.history.id_invalid": "Usage: PK战绩 (self) or PK战绩 <website ID> (admins may query others).",
        "pk.history.admin_required": "❌ Only admins can query other users' PK data.",
        "pk.history.header": "📊 Your PK today ({date})\nWebsite ID {site}",
        "pk.history.header_all": "📊 Your all-time PK records (since launch)\nWebsite ID {site}",
        "pk.history.stats": "Games {games} | W {wins} L {losses} | net {net}\nWon {win} | Lost {lose}\nAs challenger {chal} | as acceptor {opp}\n— recent games (≤5) —",
        "pk.history.line": "{time} | vs Website ID {other} | stake {stake} | {result} | digit {digit}",
        "pk.history.win": "✅ W",
        "pk.history.lose": "❌ L",
        "pk.history.no_data": "🤔 You have no PK records today.",
        # 醍醐测智
        "tihudace.disabled": "❌ 醍醐测智 is not configured: codex_settings.prompt_template is empty.",
        "tihudace.working": "🎨 Calling {model} to draw an SVG animation using the configured prompt, please wait…",
        "tihudace.model_timeout": "❌ Model generation timed out; no image was generated. Try again later; scheduled runs continue next cycle.",
        "tihudace.model_failed": "❌ The model request failed or returned an empty result. Check the model service and plugin logs.",
        "tihudace.run.status": "Latest run since startup: {state} | Elapsed: {elapsed} seconds",
        "tihudace.run.IDLE": "not triggered yet",
        "tihudace.run.GENERATING": "generating image",
        "tihudace.run.SENDING": "sending messages",
        "tihudace.run.OK": "all channel send calls succeeded (not client display confirmation)",
        "tihudace.run.MODEL_TIMEOUT": "model generation timed out; next cycle will continue",
        "tihudace.run.MODEL_FAILED": "model request failed or returned empty content",
        "tihudace.run.HTML_MISSING": "model content is not HTML/SVG",
        "tihudace.run.RENDER_FAILED": "image rendering failed",
        "tihudace.run.DISABLED": "generation prompt is not configured",
        "tihudace.run.PUSH_FAILED": "some or all pushes failed; check logs",
        "tihudace.run.NO_TARGETS": "image generated but no push targets configured",
        "tihudace.run.CANCELLED": "run cancelled",
        "tihudace.run.INTERNAL_ERROR": "unexpected error; check logs",
        "tihudace.html_missing": "❌ The model did not return renderable HTML/SVG content.",
        "tihudace.render_failed": "❌ Image rendering failed (Chromium missing in container or render timed out).",
        # Batch testing across multiple sites
        "tihudace.batch.working": "🎨 Batch test: requesting {count} site(s) and rendering each result, please wait…",
        "tihudace.batch.header": "🧪 Batch results: {ok}/{total} site(s) produced an image",
        "tihudace.batch.item": "✅ {name} | {model}: {state}",
        "tihudace.batch.state.ok": "image produced",
        "tihudace.batch.item_failed": "❌ {name} | {model}: {reason}",
        "tihudace.batch.none": "No site produced an image. Check the site URLs, keys, model names and plugin logs.",
        "tihudace.batch.issue_header": "⚠️ These site entries are invalid and were skipped (keys masked):",
        "tihudace.batch.issue_line": "Entry {index} ({preview}): {reason}",
        "tihudace.batch.issue_more": "{count} more invalid entries not shown.",
        "tihudace.spec.reason.invalid_format": "invalid format; use url|api_key|model or name|url|api_key|model",
        "tihudace.spec.reason.invalid_base_url": "invalid site URL; it must be an http(s) URL",
        "tihudace.spec.reason.missing_model": "missing model name",
        "tihudace.spec.reason.missing_api_key": "missing API key (entry and api_key are both empty)",
        "tihudace.spec.reason.too_many_targets": "exceeds the per-batch limit (10 sites)",
        "tihudace.push.group_only": "Run this command in a QQ Official ordinary group or a OneBot group, not a private chat or QQ guild channel.",
        "tihudace.push.admin_only": "This operation requires AstrBot admin permission (QQ Official identifies admins by sender OpenID).",
        "tihudace.push.bad_number": "Enter a valid group number: 醍醐推送绑定 <number> / 醍醐推送解绑 <number>. A OneBot number must match the current group.",
        "tihudace.push.bad_store": "Invalid group binding data; no changes were made. Ask an administrator to inspect it.",
        "tihudace.push.session": "Current QQ group push session (paste into the QQ push group list):\n{session}\nAlternatively, list the numeric group number and ask an AstrBot admin to run 醍醐推送绑定 <number> in this group.",
        "tihudace.push.conflict": "Group alias {group} is already bound to another session; it was not overwritten. Run 醍醐推送解绑 {group} first, then bind in the correct group.",
        "tihudace.push.bound": "✅ Group-number alias {group} is bound to this QQ group.\n{state}\nThe official API cannot verify numeric group numbers. Confirm that this is the intended group; binding does not immediately send a push.",
        "tihudace.push.selected": "This group number is selected; scheduled jobs will attempt to send here.",
        "tihudace.push.not_selected": "This group number is not selected. Add it under plugin settings → scheduled Tihudace pushes → QQ push group list.",
        "tihudace.push.unbound": "Unbound group-number alias {group}. To stop pushes, also remove the number and any direct session entry from the QQ push group list.",
        "tihudace.push.status": "Tihudace QQ push status\nSchedule: {enabled} | Interval: {interval} minutes\nSelected targets: {count} | Routable groups: {ready} (not a delivery receipt)",
        "tihudace.push.issue": "Target {target} is not ready: {reason}",
        "tihudace.push.reason.invalid_configuration": "Invalid entry; enter a group number or a complete group session.",
        "tihudace.push.reason.platform_unavailable": "The corresponding bot instance is missing or disabled.",
        "tihudace.push.reason.invalid_binding": "Invalid binding data; unbind and bind again.",
        "tihudace.push.reason.unbound_group_number": "Unbound group number; run 醍醐推送绑定 <number> in the intended group.",
        "tihudace.push.reason.invalid_group_id": "The group identifier does not match this platform's format.",
        "pk.self": "🤦 You cannot challenge yourself.",
        "pk.target_not_found": "❌ No binding found for target {id}.",
        "pk.already_pending": "⏳ You already have a pending PK with this opponent. Wait for them to accept or for it to expire.",
        "pk.insufficient_balance": "❌ Insufficient balance, PK cannot start: {need} needed, current {balance}.",
        "pk.deduct_failed": "❌ Stake deduction failed; the challenge was not created. Please retry or contact the admin.",
        "pk.db_failed": "❌ Failed to create the PK record; the stake has been refunded.",
        "pk.created": "⚔️ Website ID {challenger} challenged Website ID {opponent}!\n💰 Each side stakes {amount} quota (challenger already deducted)\n👉 The opponent must send \"接受PK {challenger}\" within {minutes} minutes\n⏰ It expires and refunds automatically otherwise.",
        "pk.accept.not_found": "🤔 No PK is waiting for you (it may have expired already).",
        "pk.accept.multiple": "🤔 {count} PK challenges are waiting for you; specify the challenger: 接受PK <website ID>",
        "pk.accept.expired": "⏰ This PK already expired and the challenger's stake was refunded.",
        "pk.accept.insufficient": "❌ Insufficient balance to accept the PK: {need} needed, current {balance}.",
        "pk.accept.deduct_failed": "❌ Stake deduction failed while accepting; please retry.",
        "pk.settled": "⚔️ PK settled!\n🕐 Settled ms timestamp: {time}\n🧮 Process: {expression} = {digit_sum} → last digit {digit} ({parity})\n🏆 Winner: Website ID {winner} (received {pot} quota)\n💸 Loser: Website ID {loser}\n💰 Balances after settlement:\n  Website ID {winner} (W) → {winner_balance}\n  Website ID {loser} (L) → {loser_balance}\n📜 Rule: sum all digits of the timestamp, take the last digit; even = challenger wins, odd = acceptor wins.",
        "pk.parity_odd": "odd",
        "pk.parity_even": "even",
        "pk.balance_unavailable": "unavailable",
        "pk.settle_failed": "❌ PK settlement failed; both stakes were refunded where possible. Check your balance with 查询余额 and contact the admin if anything is missing.",
        "pk.auto_failed": "⚔️ The PK was auto-accepted but could not settle (admin balance insufficient or deduction failed); your stake has been refunded.",
    },
}


_TRANSLATIONS["zh"].update({
    "common.at_or_id_required": "请提供网站ID、QQ号或OpenID，或 @ 一个目标。官机未传递目标提及时，请直接输入网站ID。",
    "target.too_many": "一次只能指定一个目标，请移除多余的 @ 提及。",
    "query_other.label_openid": "OpenID",
    "lookup.header": "✅ 查询成功！输入类型：{label}\n网站ID：{site_id}",
    "lookup.identity": "{label}：{identity}\n绑定时间：{time}",
    "adjust.usage": "用法：调整余额 网站ID 金额，例如：调整余额 13 1.25（增加）或 调整余额 13 -1.25（扣减）。也支持 QQ号、OpenID 或实际 @目标。",
    "adjust.invalid_amount": "额度必须是有限且非零的数字，换算后至少为 1 个原始额度单位。",
    "adjust.applied_unavailable": "✅ 网站ID {site_id} 的额度已调整，但暂时未能读取最新余额。请用「查余额」核对，不要重复执行本次调整。",
    "bind.openid_disabled": "当前是官方机器人身份。请先在插件配置中开启 OpenID 绑定，再发送绑定指令。",
    "bind.already_bound": "当前身份已绑定网站ID {site_id}，无需重复绑定。",
    "unbind.success": "✅ 网站ID {site_id} 的全部 QQ/OpenID 绑定已解除（原身份：{qq}）。",
    "heist.no_target": "🤔 未识别到目标。请从QQ成员列表真正 @ 一位已绑定的成员，或发送「打劫 网站ID」（例如「打劫 13」）。手工输入的 @昵称 不包含账号身份，无法用于查找。",
})
_TRANSLATIONS["en"].update({
    "common.at_or_id_required": "Enter a website ID, QQ number or OpenID, or mention one target. Use a website ID when the official bot does not receive member mentions.",
    "target.too_many": "Please specify only one target.",
    "query_other.label_openid": "OpenID",
    "lookup.header": "✅ Found ({label})\nWebsite ID: {site_id}",
    "lookup.identity": "{label}: {identity}\nBound at: {time}",
    "adjust.usage": "Usage: 调整余额 target amount, e.g. 调整余额 13 1.25 or 调整余额 13 -1.25. Target may be a website ID, QQ, OpenID or a real mention.",
    "adjust.invalid_amount": "Amount must be finite and nonzero, and worth at least one raw quota unit.",
    "adjust.applied_unavailable": "✅ Quota adjustment for website ID {site_id} was applied, but the latest balance could not be read. Query the balance; do not repeat this adjustment.",
    "bind.openid_disabled": "Enable OpenID binding in plugin settings before binding through the official bot.",
    "bind.already_bound": "This identity is already bound to website ID {site_id}.",
    "unbind.success": "✅ Removed all QQ/OpenID bindings for website ID {site_id} (previous identity: {qq}).",
    "heist.no_target": "No target identity received. Use 打劫 <website ID> (e.g. 打劫 13), QQ, OpenID or a real mention. A display name alone cannot identify an account.",
})

# 插件反馈 issue 入口（报错文案统一附带）
_ISSUE_URL = "https://github.com/Tianxu-Astrbot-Plugins/astrbot_plugin_newapi_tx/issues"
_ISSUE_FOOTER_ZH = f"\n\n🛠 遇到问题？请到 {_ISSUE_URL} 反馈（建议附上后台日志，我们会尽快处理）"
_ISSUE_FOOTER_EN = f"\n\n🛠 Having trouble? Report at {_ISSUE_URL} (backend logs appreciated)"

# 视为「插件内部失败」的文案 key：translate() 会自动附上反馈链接。
# 用户输入校验类提示（如 ID 格式错误、未绑定、黑名单）不算报错，不在此列。
_ERROR_KEYS = frozenset({
    "common.unexpected_error",
    "bind.failed",
    "unbind.failed",
    "check_in.api_user_not_found",
    "check_in.api_update_failed",
    "check_in.unknown",
    "query_balance.failed",
    "query_other.failed",
    "search.failed",
    "adjust.fetch_failed",
    "adjust.update_failed",
    "heist.api_error",
    "heist.unknown",
    "pk.deduct_failed",
    "pk.db_failed",
    "pk.accept.deduct_failed",
    "pk.settle_failed",
    "tihudace.render_failed",
    "rp.api_error",
    "db_transfer.failed",
})


def translate(lang: str, key: str, **kwargs):
    """按语言返回翻译后的文案；缺 key 或语言时回退到中文，再回退到 key 本身。

    命中 ERROR_KEYS（插件内部失败类文案）时自动追加 issue 反馈链接，方便用户上报。
    """
    lang_key = "en" if str(lang).lower().startswith("en") else "zh"
    msg = _TRANSLATIONS.get(lang_key, _TRANSLATIONS["zh"]).get(key)
    if msg is None:
        msg = _TRANSLATIONS["zh"].get(key, key)
    if kwargs:
        try:
            msg = msg.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    if key in _ERROR_KEYS:
        msg += _ISSUE_FOOTER_ZH if lang_key == "zh" else _ISSUE_FOOTER_EN
    return msg
    return msg
