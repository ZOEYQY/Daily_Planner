// Setup: run this once inside the Scripting app. It asks for your personal
// widget link (Daily Planner → Home → Home-screen widget → 复制我的链接),
// checks it works, and saves it on this phone only.
import { Script, Widget } from "scripting"

const LINK_RE = /^https?:\/\/\S+\/calendar\/widget\/[^/\s]+\/[0-9a-f]{16,}\.json$/i

async function readClipboardLink(): Promise<string | null> {
  try {
    const text = ((await Pasteboard.getString()) || "").trim()
    return LINK_RE.test(text) ? text : null
  } catch {
    return null
  }
}

async function check(url: string): Promise<string | null> {
  try {
    const res = await fetch(url)
    if (!res.ok) return res.status === 404 ? "这个链接不对（404）。请回网站重新复制。" : `网站回应 ${res.status}，等一下再试。`
    const data = await res.json()
    if (!data || !Array.isArray(data.days)) return "这个链接拿到的不是时间表。"
    Storage.set("feed", data)
    return null
  } catch (e) {
    return "连不上网站，检查网络后再试。（Render 免费版第一次打开要等大约 1 分钟）"
  }
}

async function main() {
  const saved = Storage.get<string>("feedUrl")
  const fromClipboard = await readClipboardLink()

  let url: string | null = null
  if (fromClipboard && fromClipboard !== saved) {
    const ok = await Dialog.confirm({
      title: "Daily Planner",
      message: "剪贴板里有你的小组件链接，要用它吗？",
      confirmLabel: "用这个",
      cancelLabel: "不要",
    })
    if (ok) url = fromClipboard
  }
  if (!url) {
    url = await Dialog.prompt({
      title: "贴上你的小组件链接",
      message: "在 Daily Planner 网站：Home → Home-screen widget → 复制我的链接",
      defaultValue: saved || "",
      placeholder: "https://…/calendar/widget/….json",
      confirmLabel: "保存",
      cancelLabel: "取消",
    })
  }
  if (url == null) {
    Script.exit()
    return
  }
  url = url.trim()
  if (!LINK_RE.test(url)) {
    await Dialog.alert({ title: "链接不对", message: "要贴的是网站上「复制我的链接」那一条（以 .json 结尾）。" })
    Script.exit()
    return
  }
  const problem = await check(url)
  if (problem) {
    await Dialog.alert({ title: "还不行", message: problem })
    Script.exit()
    return
  }
  Storage.set("feedUrl", url)
  Storage.remove("hourStart")
  Storage.remove("dayOffset")
  Storage.remove("manualAt")
  Widget.reloadAll()
  await Dialog.alert({
    title: "好了 ✓",
    message:
      "回到主屏幕：\n" +
      "1. 长按空白处 → 左上角「＋」→ 找 Scripting → 选「大号」→ 添加。\n" +
      "2. 再加一个「中号」放在它正下方。\n" +
      "3. 长按每个小组件 → 编辑小组件 → Script 选 Daily Planner。",
    buttonLabel: "知道了",
  })
  Script.exit()
}

main()
