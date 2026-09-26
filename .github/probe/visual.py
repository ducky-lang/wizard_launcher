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

for step in range(16):
    time.sleep(6)
    key("f")
time.sleep(35)
shot("room")

def command(text):
    key("t")
    time.sleep(1)
    xdo("type", "--delay", "30", text)
    key("Return")
    time.sleep(1)

pack = os.path.join(DATA, "resources/servers/1.16.5/world/datapacks/wiztest")
os.makedirs(os.path.join(pack, "data/wiztest/functions"), exist_ok=True)
open(os.path.join(pack, "pack.mcmeta"), "w").write('{"pack":{"pack_format":6,"description":"test"}}')
F = os.path.join(pack, "data/wiztest/functions")
open(os.path.join(F, "fx.mcfunction"), "w").write("\n".join([
    "effect give @s minecraft:levitation 1000 255 true",
    "execute at @s run summon armor_stand ^-1 ^-0.5 ^3 {NoGravity:1b,Invisible:1b,Tags:[\"wiztest\"],ArmorItems:[{},{},{},{id:\"minecraft:iron_hoe\",Count:1b,tag:{Unbreakable:1b,Damage:164,HideFlags:63}}]}",
    "execute at @s run summon armor_stand ^1 ^-0.5 ^3 {NoGravity:1b,Tags:[\"wiztest\"],HandItems:[{id:\"minecraft:golden_shovel\",Count:1b,tag:{Unbreakable:1b,Damage:30}},{}],ArmorItems:[{},{},{},{id:\"minecraft:diamond_hoe\",Count:1b,tag:{Unbreakable:1b,Damage:1285}}]}",
    "execute at @s run summon armor_stand ^ ^-0.5 ^3 {NoGravity:1b,Tags:[\"wiztest\"],ArmorItems:[{},{},{},{id:\"minecraft:gold_block\",Count:1b}]}",
]) + "\n")
open(os.path.join(F, "particles.mcfunction"), "w").write("\n".join([
    "execute at @s run particle minecraft:dust 0.35 1 0.35 3 ^ ^1.3 ^2 0.4 0.4 0.4 10 60 force @a",
    "execute at @s run particle minecraft:cloud ^-1.5 ^1.3 ^2.5 0.2 0.2 0.2 0.02 40 force @a",
    "execute at @s run particle minecraft:flame ^1.5 ^1.3 ^2.5 0.2 0.2 0.2 0.01 40 force @a",
    "execute at @s run particle minecraft:angry_villager ^ ^2 ^2.5 0.5 0.2 0.5 0.01 20 force @a",
    "execute at @s run particle minecraft:falling_dust minecraft:gray_wool ^ ^2 ^2.5 0.5 0.5 0.5 1 30 force @a",
    "execute at @s run summon area_effect_cloud ^ ^1 ^2 {Radius:1.0f,Duration:200,Particle:\"dust 1 0 0 2\"}",
]) + "\n")
command("/reload")
time.sleep(8)
command("/datapack enable \"file/wiztest\"")
time.sleep(3)
key("F1")
command("/function wiztest:fx")
time.sleep(4)
shot("models")
for i in range(3):
    command("/function wiztest:particles")
    time.sleep(0.4)
    shot(f"particles-{i}")
key("F1")
time.sleep(1)
command("/advancement grant @s only hp:quests/root")
time.sleep(1)
shot("root-0")
time.sleep(7)
command("/advancement grant @s only hp:quests/apparition_new")
for i, d in enumerate([0.3, 0.8, 1.5, 2.5]):
    time.sleep(d)
    shot(f"toast-{i}")
time.sleep(6)
command("/advancement grant @s until hp:ui/ui28_void")
time.sleep(3)
shot("tips")
time.sleep(5)
key("l")
time.sleep(2)
shot("advscreen")
key("Escape")
time.sleep(1)
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
    if "WizardLegacyPacks" in l or "wizard_legacy_packs" in l or "dvancement" in l or "article" in l or "oast" in l:
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
print("@@CONFIG")
for root, _, files in os.walk(os.path.join(game, "config")):
    for f in files:
        full = os.path.join(root, f)
        if any(k in f.lower() for k in ["sodium", "extra", "particle", "toast", "immediatelyfast", "moreculling", "entityculling", "iris"]):
            print("@@CFG", os.path.relpath(full, game))
            print(open(full, encoding="utf-8", errors="replace").read()[:4000])
opt = os.path.join(game, "options.txt")
if os.path.exists(opt):
    print("@@OPTIONS")
    print(open(opt, encoding="utf-8", errors="replace").read()[:6000])
print("@@PLAYLOG")
print(open("play.log", encoding="utf-8", errors="replace").read()[-6000:])
if False:
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
