// Daily Planner 小组件安装器
// 用法：在 Scripting 新建一个脚本，名字叫 Daily Planner，把这整段贴进去
// （取代原本的全部内容），按 ▶ 运行一次。它会下载小组件的程式并存好你的链接。
// 这段里有你专属的链接，不要发给别人。
import { Script, Widget } from "scripting"

const SITE = "__SITE__"
const FEED_URL = "__FEED_URL__"
const FILES = ["widget.tsx", "app_intents.tsx", "index.tsx"]

async function install() {
  for (const name of FILES) {
    const res = await fetch(SITE + "/calendar/widget/code/" + name)
    if (!res.ok) throw new Error("下载 " + name + " 失败（" + res.status + "）")
    const text = await res.text()
    await FileManager.writeAsString(Script.directory + "/" + name, text)
  }
  Storage.set("feedUrl", FEED_URL)
  Widget.reloadAll()
  await Dialog.alert({
    title: "装好了 ✓",
    message:
      "回到主屏幕：\n" +
      "1. 长按空白处 → 编辑 → 添加小组件 → 找 Scripting → 选「大号」→ 添加。\n" +
      "2. 再加一个「中号」放在它正下方。\n" +
      "3. 长按每个小组件 → 编辑小组件 → Script 选 Daily Planner。",
    buttonLabel: "知道了",
  })
}

install()
  .catch(async (e) => {
    await Dialog.alert({
      title: "没装好",
      message: String(e) + "\n\n检查网络后再按一次 ▶。网站第一次打开可能要等 1 分钟。",
    })
  })
  .finally(() => Script.exit())
