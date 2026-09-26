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

def shot(label):
    png = subprocess.run(["import", "-window", "root", "png:-"], env=env, capture_output=True).stdout
    jpg = subprocess.run(["convert", "png:-", "-resize", "1024x", "-quality", "72", "jpg:-"], input=png, capture_output=True).stdout
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
for i in range(3):
    shot(f"idle{i}")
    time.sleep(0.4)
for i in range(3):
    xdo("click", "5")
    time.sleep(2.5)
    shot(f"scrolldown{i+1}")
for i in range(3):
    xdo("click", "4")
    time.sleep(2.5)
    shot(f"scrollup{i+1}")
time.sleep(5)
shot("after")
t = text()
chat = [l for l in t.splitlines() if "[CHAT]" in l]
print(f"@@CHAT {len(chat)} lines")
for l in chat[-120:]:
    print("CHAT", repr(l[-400:]))
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
if MC == "1.21.1":
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
