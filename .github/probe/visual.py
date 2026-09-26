import base64, glob, json, os, re, subprocess, sys, time

MC = sys.argv[1]
DATA = os.environ["WIZARD_LAUNCHER_DATA"]
os.makedirs(DATA, exist_ok=True)
json.dump({"game_width": 1280, "game_height": 720}, open(os.path.join(DATA, "settings.json"), "w"))
env = dict(os.environ, DISPLAY=":99")
subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x720x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
BIN = "launcher-app/build/install/WizardLauncher/bin/WizardLauncher"
out = open("play.log", "w")
play = subprocess.Popen([BIN, "--play", "--name", "WizardTest", "--instance", MC], stdout=out, stderr=subprocess.STDOUT, env=env)
game = os.path.join(DATA, "resources/client", MC, "minecraft")
log = os.path.join(game, "logs/latest.log")

def text():
    try:
        return open(log, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""

def shot(label, crop=None):
    png = subprocess.run(["import", "-window", "root", "png:-"], env=env, capture_output=True).stdout
    args = ["-crop", crop] if crop else ["-resize", "1024x"]
    jpg = subprocess.run(["convert", "png:-", *args, "-quality", "72", "jpg:-"], input=png, capture_output=True).stdout
    print(f"@@SHOT {label} {base64.b64encode(jpg).decode()}", flush=True)

def xdo(*args):
    subprocess.run(["xdotool", *args], env=env)

deadline = time.time() + 1500
while time.time() < deadline and play.poll() is None:
    if "Connecting to" in text():
        break
    time.sleep(5)
print("connect seen", "Connecting to" in text(), "play alive", play.poll() is None, flush=True)
joined = time.time()
while time.time() < joined + 240 and play.poll() is None:
    if "[CHAT]" in text() or "Loaded" in text() and time.time() > joined + 90:
        break
    time.sleep(5)
time.sleep(40)
shot("spawn")
win = subprocess.run(["xdotool", "search", "--name", "Minecraft"], env=env, capture_output=True, text=True).stdout.split()
print("windows", win, flush=True)
if win:
    xdo("windowactivate", "--sync", win[0])
    xdo("mousemove", "640", "360")
time.sleep(2)

def burst(label, n=3, gap=0.35):
    for i in range(n):
        shot(f"{label}-{i}")
        time.sleep(gap)

def key(k):
    xdo("key", "--delay", "80", k)

key("f")
time.sleep(4)
shot("f1")
QUICK = os.environ.get("VISUAL_QUICK") == "1"
for step in range(16):
    time.sleep(6)
    if not QUICK:
        burst(f"s{step:02d}", 2 if step % 3 else 3)
    if step in (5, 9, 13) and not QUICK:
        xdo("click", "4")
        time.sleep(1.5)
        shot(f"s{step:02d}-up")
    key("f")
time.sleep(5)
shot("end")
time.sleep(30)
shot("room")

def command(text):
    key("t")
    time.sleep(1)
    xdo("type", "--delay", "40", text)
    key("Return")

command("/effect give @s minecraft:levitation 1000 255 true")
time.sleep(2)
command("/execute at @s run summon armor_stand ^ ^ ^3 {NoGravity:1b,ShowArms:1b,Rotation:[180f,0f],ArmorItems:[{},{},{id:\"minecraft:diamond_chestplate\",Count:1b},{id:\"minecraft:gold_block\",Count:1b}],Passengers:[{id:\"minecraft:armor_stand\",ShowArms:1b,ArmorItems:[{},{},{id:\"minecraft:iron_chestplate\",Count:1b},{id:\"minecraft:diamond_block\",Count:1b}],Passengers:[{id:\"minecraft:armor_stand\",Small:1b,ArmorItems:[{},{},{},{id:\"minecraft:emerald_block\",Count:1b}]}]}]}")
time.sleep(4)
key("F1")
time.sleep(1)
shot("stack")
t = text()
chat = [l for l in t.splitlines() if "[CHAT]" in l]
print(f"@@CHAT {len(chat)} lines")
for l in chat[-120:]:
    print("CHAT", repr(l[-400:]))
print("@@MOD")
lines = t.splitlines()
shown = 0
for i, l in enumerate(lines):
    if shown > 80:
        break
    if "REFMAP" in l:
        for c in lines[max(0, i - 4):i + 3]:
            print("R", c[:400])
    if "WizardLegacyPacks" in l or "wizard_legacy_packs" in l or ("ixin" in l and "wizard" in l.lower()):
        print("M", l[:500])
        shown += 1
print("@@WARN")
seen = set()
for l in t.splitlines():
    if ("/WARN]" in l or "/ERROR]" in l) and l[30:120] not in seen:
        seen.add(l[30:120])
        print("W", l[:400])
        if len(seen) > 150:
            break
for f in glob.glob(os.path.join(game, "logs/wizard-legacy-packs/*")):
    print("@@REPORT", f)
    print(open(f, encoding="utf-8", errors="replace").read()[:15000])
print("@@PLAYLOG")
print(open("play.log", encoding="utf-8", errors="replace").read()[-6000:])
if MC == "never":
    fn = os.path.join(DATA, "resources/servers/1.16.5/world/datapacks/hp/data/hp/functions")
    tick = glob.glob(os.path.join(DATA, "resources/servers/1.16.5/world/datapacks/hp/data/minecraft/tags/functions/*.json"))
    for f in tick:
        print("@@FILE", f, open(f).read())
    for rel in ["input/input.mcfunction", "draw_hotbar/draw_main_menu.mcfunction", "conversation/tellraw_conversation.mcfunction",
                "conversation/in_conversation_player1.mcfunction", "misc/update_bossbars.mcfunction", "conversation/exit_conversation.mcfunction"]:
        p = os.path.join(fn, rel)
        if os.path.exists(p):
            print("@@FILE", rel)
            print(open(p, encoding="utf-8", errors="replace").read()[:12000])
    dirs = {}
    for root, _, files in os.walk(fn):
        dirs[os.path.relpath(root, fn)] = len(files)
    print("@@DIRS", json.dumps(dirs))
    npc = []
    for root, _, files in os.walk(os.path.join(fn, "npc")):
        for f in files:
            s = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            if re.search(r"tp @s ~ ~[-0-9.]+ ~|Pose|Motion|teleport @s", s):
                npc.append((os.path.relpath(os.path.join(root, f), fn), s[:1500]))
    for name, s in npc[:12]:
        print("@@NPC", name)
        print(s)
play.kill()
