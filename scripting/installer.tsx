// Daily Planner 小组件安装器 —— 要贴在「Scripting」app 里（不是 Scriptable）。
// 用法：在 Scripting 新建一个脚本，名字叫 Daily Planner，把这整段贴进去
// （取代原本的全部内容），按 ▶ 运行一次（几秒就会自己结束）。然后去主屏幕加小组件。
// 看进度：按下面中间的 >_ 按钮。
// 这段里有你专属的链接，不要发给别人。
// (Plain JavaScript on purpose: no import line, so pasting it anywhere parses.)

var SITE = "__SITE__"
var FEED_URL = "__FEED_URL__"
var FILES = ["widget.tsx", "app_intents.tsx", "index.tsx"]

function say(msg) {
  try { console.log("[Daily Planner] " + msg) } catch (e) {}
}

function scriptDir() {
  if (typeof Script !== "undefined" && Script && typeof Script.directory === "string" && Script.directory) {
    return Script.directory
  }
  if (typeof FileManager !== "undefined" && FileManager.scriptsDirectory) {
    return FileManager.scriptsDirectory + "/Daily Planner"
  }
  return null
}

async function install() {
  say("开始安装")
  say("Script: " + typeof Script + " / FileManager: " + typeof FileManager + " / fetch: " + typeof fetch)
  var dir = scriptDir()
  say("脚本资料夹: " + dir)
  if (!dir) throw new Error("找不到脚本资料夹（是不是开在 Scriptable 了？要用 Scripting）")
  for (var i = 0; i < FILES.length; i++) {
    var name = FILES[i]
    say("下载 " + name + " …")
    var res = await fetch(SITE + "/calendar/widget/code/" + name)
    if (!res.ok) throw new Error("下载 " + name + " 失败（" + res.status + "）")
    var text = await res.text()
    say("写入 " + name + "（" + text.length + " 字）")
    await FileManager.writeAsString(dir + "/" + name, text)
  }
  Storage.set("feedUrl", FEED_URL)
  say("✓ 装好了！回主屏幕加小组件（Scripting → 大号 → Script 选 Daily Planner）")
}

install()
  .catch(function (e) {
    say("✗ 出错: " + e)
  })
  .finally(function () {
    say("结束")
    try { if (typeof Script !== "undefined" && Script && Script.exit) Script.exit() } catch (e) {}
  })
