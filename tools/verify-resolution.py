#!/usr/bin/env python3
"""Weryfikacja poprawki 'rozdzielczosc renderera = rozdzielczosc gry' na instancji.

Mierzy trzy rzeczy, kazda inna niz poprzednia:
 1. czy renderer wczytal sie w rozdzielczosci GRY (linia z logu o nadpisaniu),
 2. czy zmiana z menu F11 trafia takze do GOTHIC.INI (druga polowa poprawki),
 3. czy wskaznik faktycznie obraca kamera w swiecie (metoda straznika obrazu: roznica klatek).
"""
import os
import re
import subprocess
import time

SYS = "/workspace/Games/Gothic2-NB/prefix/drive_c/GOG Games/Gothic 2 Gold/system"
LOG = SYS + "/Log.txt"
GOTHIC_INI = SYS + "/GOTHIC.INI"
RENDERER_INI = SYS + "/GD3D11/UserSettings.ini"
os.environ["DISPLAY"] = ":99"


def sh(cmd, t=120):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as exc:
        return "ERR:" + type(exc).__name__


def frame(path="/tmp/v.png"):
    sh("rm -f %s; DISPLAY=:99 timeout 12 ffmpeg -hide_banner -loglevel error -f x11grab "
       "-video_size 1920x1080 -i :99 -frames:v 1 -y %s 2>/dev/null" % (path, path))
    return os.path.getsize(path) if os.path.exists(path) else 0


def ini_key(path, section, key):
    out = sh("awk 'BEGIN{IGNORECASE=1} /^\\[/{s=$0} /^%s=/{if (s ~ /%s/) {print $0; exit}}' '%s'"
             % (re.escape(key), re.escape(section), path))
    return out.strip()


print("== 1. wersja renderera i linia o zgodnosci rozdzielczosci ==")
print("   wersja:", sh("grep -a 'GD3D11 Version' '%s' | tail -1" % LOG) or "(brak)")
rec = sh("grep -aE 'Rozdzielczosc gry|game resolution .* overrides' '%s' | tail -2" % LOG)
print("   zgodnosc rozdzielczosci:", rec or "(brak linii - albo sie zgadzaly, albo poprawka nie dziala)")

print("== 2. obie rozdzielczosci: gra vs renderer ==")
print("   GOTHIC.INI [VIDEO]:", ini_key(GOTHIC_INI, "VIDEO", "zVidResFullscreenX"),
      ini_key(GOTHIC_INI, "VIDEO", "zVidResFullscreenY"))
print("   renderer [Display]: ", ini_key(RENDERER_INI, "Display", "Width"),
      ini_key(RENDERER_INI, "Display", "Height"))

print("== 3. obraz: menu -> swiat (F12), potem reakcja na ruch wskaznika ==")
gid = sh("DISPLAY=:99 xdotool search --name Gothic 2>/dev/null | head -1")
print("   okno gry:", gid or "BRAK")
if gid:
    sh("DISPLAY=:99 xdotool windowraise %s; DISPLAY=:99 xdotool windowfocus --sync %s" % (gid, gid))
print("   klatka w menu:", frame(), "B")
sh("DISPLAY=:99 xdotool key --clearmodifiers F12")
time.sleep(20)
in_world = frame()
print("   klatka w swiecie:", in_world, "B", "(~3 MB = swiat)")

if in_world > 1500000:
    # Metoda straznika: przy zablokowanym wskazniku ruch kursora obraca kamere, wiec klatki
    # roznia sie wyraznie. Bez fokusu/przy rozjechanej rozdzielczosci roznice sa sladowe.
    diffs = []
    for _ in range(4):
        sh("DISPLAY=:99 xdotool mousemove_relative -- 120 0; "
           "DISPLAY=:99 xdotool mousemove_relative -- -120 0")
        time.sleep(1)
        diffs.append(frame("/tmp/v2.png"))
    base = in_world
    print("   klatki po ruchu wskaznika:", diffs, "B (bazowa %d)" % base)
    print("   >>> wskaznik obraca kamera" if any(abs(d - base) > 40000 for d in diffs)
          else "   >>> UWAGA: brak reakcji na wskaznik")
else:
    print("   (nie wszedlem do swiata - pomijam test wskaznika)")

print("== 4. braki tekstur / bledy ==")
print("   'Failed to load texture':", sh("grep -ac 'Failed to load texture' '%s'" % LOG) or "0")
print("   proces gry:", sh("pgrep -c -x Gothic2.exe") or "0")
