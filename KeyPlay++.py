#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Synthesia Piano para macOS  -  reproductor estilo Synthesia con SoundFonts (.sf2)

Complemento de MIDI++ (midiplusplus_mac.py). Recibe:
  * MIDI      : cualquier teclado/dispositivo MIDI, o el "Puerto MIDI virtual" de MIDI++
                (aparece como "MIDI++ Out"), o su propio puerto virtual "Piano Synthesia In".
  * QWERTY    : el mismo mapa de Virtual Piano 88 de MIDI++ (blancas = tecla, negras = Shift+tecla,
                extremos = Ctrl+tecla, Espacio = sustain). Funciona con el teclado real o con las
                teclas que MIDI++ envía, solo cuando la ventana está al frente.
  * MidiConnect : decodifica el protocolo numérico de MIDI++ (teclado numérico: * + 4 dígitos).
Además reproduce archivos .mid con caída de notas estilo Synthesia.

INSTALACIÓN (una vez, en Terminal):
    brew install fluid-synth
    python3 -m pip install mido python-rtmidi pyfluidsynth pyobjc-framework-Quartz \
        pyobjc-framework-Cocoa

EJECUTAR:
    python3 synthesia_piano.py

PERMISOS: Ajustes del Sistema > Privacidad y seguridad > Accesibilidad y Monitorización de entrada
          -> activa la app desde la que lo ejecutas (Terminal, iTerm, VS Code...).
          (necesario para capturar QWERTY y MidiConnect con la ventana al frente)

CARPETAS (junto al script):
    *.sf2 / soundfonts/   SoundFonts (se buscan aquí; "Importar .sf2" copia a soundfonts/)
    midi/                 tus .mid (también se buscan junto al script)

TECLAS (con la ventana en primer plano)
    ↑ / ↓  transposición +1 / −1 semitono        ← / →  volumen −5 % / +5 %
"""

import os
import sys
import time
import glob
import json
import bisect
import queue
import shutil
import threading
from collections import defaultdict

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import mido
except ImportError:
    sys.exit("Falta 'mido'. Ejecuta:\n  python3 -m pip install mido python-rtmidi")

import ctypes.util
_find_orig = ctypes.util.find_library


def _find_fs(nombre):
    r = _find_orig(nombre)
    if r is None and "fluidsynth" in str(nombre):
        for d in ("/usr/local/lib", "/opt/homebrew/lib"):
            for f in sorted(glob.glob(os.path.join(d, "libfluidsynth*.dylib"))):
                return f
    return r


ctypes.util.find_library = _find_fs

FS_ERR = ""
try:
    import fluidsynth
    HAY_FS = True
except Exception as _e:
    fluidsynth = None
    HAY_FS = False
    FS_ERR = f"{type(_e).__name__}: {_e}"

HAY_TAP = True
try:
    from Quartz import (
        CGEventTapCreate, CGEventTapEnable, CGEventGetIntegerValueField, CGEventGetFlags,
        kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
        kCGEventKeyDown, kCGEventKeyUp, kCGKeyboardEventKeycode, kCGKeyboardEventAutorepeat,
        kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput,
        kCGEventFlagMaskShift, kCGEventFlagMaskControl,
        kCGEventFlagMaskAlternate, kCGEventFlagMaskCommand,
        CFMachPortCreateRunLoopSource, CFRunLoopGetCurrent, CFRunLoopAddSource,
        kCFRunLoopCommonModes, CFRunLoopRun, CFRunLoopStop,
    )
except Exception:
    HAY_TAP = False
    kCGEventFlagMaskShift = kCGEventFlagMaskControl = 0
    kCGEventFlagMaskAlternate = kCGEventFlagMaskCommand = 0

try:
    from AppKit import NSWorkspace

    def pid_frontal():
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.processIdentifier() if app is not None else None
    FOCO_DISPONIBLE = True
except Exception:
    FOCO_DISPONIBLE = False

    def pid_frontal():
        return None

BASE = os.path.dirname(os.path.abspath(__file__))
DIR_SF2 = os.path.join(BASE, "soundfonts")
DIR_MIDI = os.path.join(BASE, "midi")
ARCH_AJUSTES = os.path.join(BASE, "synthesia_settings.json")
for _d in (DIR_SF2, DIR_MIDI):
    try:
        os.makedirs(_d, exist_ok=True)
    except OSError:
        pass

# ============================================================================
# Mapa QWERTY (idéntico al Virtual Piano 88 de MIDI++)
# ============================================================================
SHIFT, CTRL = kCGEventFlagMaskShift, kCGEventFlagMaskControl
ALT, CMD = kCGEventFlagMaskAlternate, kCGEventFlagMaskCommand

KEYCODES = {
    'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7, 'c': 8, 'v': 9,
    'b': 11, 'q': 12, 'w': 13, 'e': 14, 'r': 15, 'y': 16, 't': 17,
    '1': 18, '2': 19, '3': 20, '4': 21, '6': 22, '5': 23, '9': 25, '7': 26, '8': 28,
    '0': 29, 'o': 31, 'u': 32, 'i': 34, 'p': 35, 'l': 37, 'j': 38, 'k': 40,
    'n': 45, 'm': 46,
}
KEY_SPACE = 49
NATURALES = {0, 2, 4, 5, 7, 9, 11}

# Protocolo MidiConnect (teclado numérico): prefijo * y 4 dígitos en base 12
MC_NUMPAD = (82, 83, 84, 85, 86, 87, 88, 89, 91, 92, 78, 69)
MC_PREFIX = 67
MC_IDX = {code: i for i, code in enumerate(MC_NUMPAD)}


def layout_vp88():
    km = {}
    chars_centro = "1234567890qwertyuiopasdfghjklzxcvbnm"   # C2..C7 (36 blancas)
    chars_bajos = "1234567890qwert"                          # A0..B1 (15 notas, Ctrl)
    chars_altos = "yuiopasdfghj"                             # C#7..C8 (12 notas, Ctrl)
    idx, ultima = -1, None
    for midi in range(36, 97):
        if midi % 12 in NATURALES:
            idx += 1
            ultima = chars_centro[idx]
            km[midi] = (KEYCODES[ultima], 0)
        else:
            km[midi] = (KEYCODES[ultima], SHIFT)
    for i, ch in enumerate(chars_bajos):
        km[21 + i] = (KEYCODES[ch], CTRL)
    for i, ch in enumerate(chars_altos):
        km[97 + i] = (KEYCODES[ch], CTRL)
    return km


def _tipo_mod(fl):
    return 'c' if fl and (fl & CTRL) else ('s' if fl and (fl & SHIFT) else 'n')


# (keycode, tipo) -> nota MIDI
REV_QWERTY = {(kc, _tipo_mod(fl)): n for n, (kc, fl) in layout_vp88().items()}

# ============================================================================
# Geometría del teclado de 88 notas (en unidades de tecla blanca)
# ============================================================================
GEOM = {}
_w = 0
for _n in range(21, 109):
    if _n % 12 in NATURALES:
        GEOM[_n] = (_w, _w + 1, False)
        _w += 1
    else:
        GEOM[_n] = (_w - 0.3, _w + 0.3, True)
N_BLANCAS = _w  # 52

PALETA = ["#4aa3ff", "#ff7a59", "#6bd66b", "#c77dff", "#ffd166", "#06d6a0",
          "#ef476f", "#8ecae6", "#f4a261", "#90be6d", "#b5838d", "#a0c4ff",
          "#ffadad", "#bdb2ff", "#caffbf", "#fdffb6"]
ORO = "#ffcc33"
BG, PANEL, FG = "#14161b", "#1c1f26", "#e6e6e6"

GM = ["Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
      "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel",
      "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
      "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica",
      "Tango Accordion", "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
      "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar",
      "Guitar Harmonics", "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)",
      "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin", "Viola",
      "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
      "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs",
      "Voice Oohs", "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
      "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2", "Soprano Sax", "Alto Sax",
      "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute",
      "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina", "Lead 1 (square)",
      "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
      "Lead 7 (fifths)", "Lead 8 (bass+lead)", "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)",
      "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
      "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)",
      "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto",
      "Kalimba", "Bagpipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
      "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise", "Breath Noise",
      "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot"]
GM_VALORES = [f"{i:03d} {n}" for i, n in enumerate(GM)]

CH_VIVO = 15          # canal del sintetizador para lo que tocas en vivo


# ============================================================================
# Sintetizador (FluidSynth + SoundFonts)
# ============================================================================
class Sintetizador:
    def __init__(self, log):
        self.log = log
        self.fs = None
        self.sfid = None
        self.ruta = None
        self.listo = False
        self.vol = 100
        self.programas = {}
        self.lock = threading.RLock()

    def _gain(self):
        return max(0.0, self.vol / 100.0 * 0.5)

    def iniciar(self):
        if not HAY_FS:
            self.log("⚠ No se pudo importar pyfluidsynth/FluidSynth: " + FS_ERR)
            self.log("  Prueba: brew install fluid-synth && python3 -m pip install --upgrade pyfluidsynth")
            return False
        try:
            self.fs = fluidsynth.Synth(samplerate=44100.0, gain=self._gain())
            try:
                self.fs.start(driver="coreaudio")
            except Exception:
                self.fs.start()
            self.listo = True
            self.log("[Audio] FluidSynth iniciado")
            return True
        except Exception as e:
            self.log(f"⚠ No pude iniciar FluidSynth: {e}")
            return False

    def cargar_sf2(self, ruta):
        if not self.listo:
            self.log("⚠ FluidSynth no está iniciado (mira el primer mensaje ⚠ del log)")
            return False
        try:
            nuevo = self.fs.sfload(ruta, update_midi_preset=1)
        except Exception as e:
            self.log(f"⚠ Error cargando SoundFont: {e}")
            return False
        if nuevo is None or nuevo < 0:
            self.log(f"⚠ FluidSynth no pudo cargar {os.path.basename(ruta)}")
            return False
        with self.lock:
            viejo, self.sfid, self.ruta = self.sfid, nuevo, ruta
        if viejo is not None:
            try:
                self.fs.sfunload(viejo)
            except Exception:
                pass
        for ch, p in list(self.programas.items()):
            try:
                self.fs.program_change(ch, p)
            except Exception:
                pass
        return True

    def set_vol(self, pct):
        self.vol = pct
        if self.listo:
            try:
                self.fs.setting('synth.gain', self._gain())
            except Exception:
                pass

    def noteon(self, ch, n, v):
        if self.listo and 0 <= n <= 127:
            self.fs.noteon(ch, int(n), max(1, min(127, int(v))))

    def noteoff(self, ch, n):
        if self.listo and 0 <= n <= 127:
            self.fs.noteoff(ch, int(n))

    def cc(self, ch, num, val):
        if self.listo:
            self.fs.cc(ch, int(num), int(val))

    def bend(self, ch, val):
        if self.listo:
            self.fs.pitch_bend(ch, int(val))

    def program(self, ch, p):
        self.programas[ch] = int(p)
        if self.listo:
            self.fs.program_change(ch, int(p))

    def reset_canales(self):
        for ch in range(16):
            if ch == CH_VIVO:
                continue
            try:
                self.cc(ch, 121, 0)
                self.cc(ch, 64, 0)
                self.program(ch, 0)
            except Exception:
                pass

    def cerrar(self):
        if self.listo:
            try:
                self.fs.delete()
            except Exception:
                pass


# ============================================================================
# Notas en vivo (MIDI / QWERTY / MidiConnect)
# ============================================================================
class Vivo:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.act = {}       # clave -> nota que suena
        self.vis = []       # barras visuales {'n','t0','t1','k'}
        self.pedales = {}   # estado independiente de cada entrada
        self.pedal_fijo = False

    def on(self, clave, nota, vel):
        with self.lock:
            if clave in self.act:
                self.off(clave)
            n = int(nota) + self.app.transp
            if not 0 <= n <= 127:
                return
            self.app.sint.noteon(CH_VIVO, n, vel)
            self.act[clave] = n
            self.vis.append({'n': n, 't0': time.perf_counter(), 't1': None, 'k': clave})

    def off(self, clave):
        with self.lock:
            n = self.act.pop(clave, None)
            if n is None:
                return
            self.app.sint.noteoff(CH_VIVO, n)
            for v in self.vis:
                if v['k'] == clave and v['t1'] is None:
                    v['t1'] = time.perf_counter()
                    break

    def _actualizar_pedal(self):
        abajo = self.pedal_fijo or any(self.pedales.values())
        self.app.sint.cc(CH_VIVO, 64, 127 if abajo else 0)

    def pedal(self, abajo, fuente="entrada"):
        with self.lock:
            self.pedales[fuente] = bool(abajo)
            self._actualizar_pedal()

    def set_pedal_fijo(self, abajo):
        with self.lock:
            self.pedal_fijo = bool(abajo)
            self._actualizar_pedal()

    def soltar_todo(self, filtro=None):
        with self.lock:
            for k in list(self.act):
                if filtro is None or k[0] == filtro:
                    self.off(k)

    def recortar(self, ahora, ventana):
        with self.lock:
            self.vis = [v for v in self.vis if v['t1'] is None or ahora - v['t1'] < ventana]


# ============================================================================
# Lectura de MIDI y reproductor de archivos
# ============================================================================
class Cancion:
    pass


def cargar_midi(ruta):
    mid = mido.MidiFile(ruta, clip=True)
    if mid.type == 2:
        raise ValueError("Los MIDI de tipo 2 no están soportados")
    eventos, notas = [], []
    pend = defaultdict(list)
    t = 0.0
    for msg in mid:
        t += msg.time
        if msg.is_meta:
            continue
        tp = msg.type
        if tp == 'note_on' and msg.velocity > 0:
            eventos.append((t, 2, 'on', msg.channel, msg.note, msg.velocity))
            pend[(msg.channel, msg.note)].append((t, msg.velocity))
        elif tp in ('note_off', 'note_on'):
            eventos.append((t, 0, 'off', msg.channel, msg.note, 0))
            lista = pend[(msg.channel, msg.note)]
            if lista:
                ini, vel = lista.pop(0)
                notas.append((ini, t, msg.note, msg.channel, vel))
        elif tp == 'control_change':
            eventos.append((t, 1, 'cc', msg.channel, msg.control, msg.value))
        elif tp == 'program_change':
            eventos.append((t, 1, 'pc', msg.channel, msg.program, 0))
        elif tp == 'pitchwheel':
            eventos.append((t, 1, 'pb', msg.channel, msg.pitch, 0))
    for (ch, nota), lista in pend.items():
        for ini, vel in lista:
            notas.append((ini, t, nota, ch, vel))
    eventos.sort(key=lambda e: (e[0], e[1]))
    notas.sort()
    c = Cancion()
    c.ruta, c.nombre = ruta, os.path.basename(ruta)
    c.eventos, c.tiempos = eventos, [e[0] for e in eventos]
    c.notas, c.inicios = notas, [n[0] for n in notas]
    c.maxdur = max((n[1] - n[0] for n in notas), default=0.0)
    c.duracion = t
    return c


class Reproductor(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app, self.sint = app, app.sint
        self.lock = threading.RLock()
        self.song = None
        self.estado = 'stopped'          # stopped | playing | paused
        self.speed = 1.0
        self.loop = False
        self.a_pos, self.a_wall = 0.0, time.perf_counter()
        self.idx = 0
        self.sonando = defaultdict(list)  # (canal, nota original) -> [notas que suenan]
        self._quit = False

    def _pos(self):
        if self.estado == 'playing':
            return self.a_pos + (time.perf_counter() - self.a_wall) * self.speed
        return self.a_pos

    def get_pos(self):
        with self.lock:
            if not self.song:
                return 0.0
            return max(0.0, min(self._pos(), self.song.duracion))

    def cargar(self, song):
        self.stop()
        with self.lock:
            self.song = song
            self.sint.reset_canales()

    def _soltar(self):
        for (ch, _), lista in list(self.sonando.items()):
            for n in lista:
                self.sint.noteoff(ch, n)
        self.sonando.clear()
        for ch in range(16):
            if ch != CH_VIVO:
                self.sint.cc(ch, 64, 0)

    def _aplicar_estado(self, idx):
        ultimos = {}
        for e in self.song.eventos[:idx]:
            if e[2] in ('cc', 'pc', 'pb'):
                ultimos[(e[2], e[3], e[4] if e[2] == 'cc' else 0)] = e
        for e in ultimos.values():
            self._emitir(e)

    def play_pause(self):
        with self.lock:
            if not self.song:
                return
            if self.estado == 'playing':
                self.a_pos = self._pos()
                self.estado = 'paused'
                self._soltar()
                self.app.log_t("⏸ Pausa")
            else:
                if self.estado == 'stopped':
                    self.a_pos, self.idx = 0.0, 0
                self.a_wall = time.perf_counter()
                self.estado = 'playing'
                self.app.log_t("▶ Play")

    def stop(self):
        with self.lock:
            self.estado = 'stopped'
            self.a_pos, self.idx = 0.0, 0
            self._soltar()

    def seek_abs(self, seg):
        with self.lock:
            if not self.song:
                return
            self.a_pos = max(0.0, min(self.song.duracion, seg))
            self.a_wall = time.perf_counter()
            self.idx = bisect.bisect_left(self.song.tiempos, self.a_pos)
            self._soltar()
            self._aplicar_estado(self.idx)

    def seek(self, delta):
        self.seek_abs(self._pos() + delta)

    def set_speed(self, v):
        with self.lock:
            self.a_pos = self._pos()
            self.a_wall = time.perf_counter()
            self.speed = max(0.1, min(4.0, float(v)))

    def _emitir(self, e):
        _, _, k, ch, a, b = e
        if k == 'on':
            n = a if ch == 9 else a + self.app.transp
            if 0 <= n <= 127:
                self.sint.noteon(ch, n, b)
                self.sonando[(ch, a)].append(n)
        elif k == 'off':
            lista = self.sonando.get((ch, a))
            if lista:
                self.sint.noteoff(ch, lista.pop(0))
        elif k == 'cc':
            self.sint.cc(ch, a, b)
        elif k == 'pc':
            self.sint.program(ch, a)
        elif k == 'pb':
            self.sint.bend(ch, a)

    def run(self):
        while not self._quit:
            espera = 0.02
            try:
                with self.lock:
                    if self.estado == 'playing' and self.song:
                        if self.idx >= len(self.song.eventos):
                            self._soltar()
                            if self.loop:
                                self.a_pos, self.a_wall, self.idx = 0.0, time.perf_counter(), 0
                                self._aplicar_estado(0)
                            else:
                                self.estado, self.a_pos, self.idx = 'stopped', 0.0, 0
                                self.app.log_t("✔ Terminó")
                            continue
                        e = self.song.eventos[self.idx]
                        resta = (e[0] - self._pos()) / self.speed
                        if resta <= 0:
                            self._emitir(e)
                            self.idx += 1
                            continue
                        espera = min(resta, 0.01)
            except Exception as ex:
                self.app.log_t(f"⚠ Error de reproducción: {ex}")
                self.estado = 'stopped'
            time.sleep(espera)


# ============================================================================
# Captura de teclado global (QWERTY y MidiConnect)
# ============================================================================
class TapTeclado(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.tap = None
        self.rl = None
        self.held = {}          # keycode -> nota (QWERTY)
        self.mc_buf = None
        self.mc_t = 0.0

    def run(self):
        if not HAY_TAP:
            self.app.log_t("⚠ Sin pyobjc-framework-Quartz: no habrá QWERTY ni MidiConnect")
            return
        try:
            mask = (1 << kCGEventKeyDown) | (1 << kCGEventKeyUp)
            self.tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                                        kCGEventTapOptionListenOnly, mask, self._cb, None)
            if self.tap is None:
                self.app.log_t("⚠ No pude escuchar el teclado. Activa Accesibilidad y Monitorización "
                               "de entrada para Terminal/iTerm/VS Code y reinicia la app.")
                return
            fuente = CFMachPortCreateRunLoopSource(None, self.tap, 0)
            self.rl = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self.rl, fuente, kCFRunLoopCommonModes)
            CGEventTapEnable(self.tap, True)
            CFRunLoopRun()
        except Exception as e:
            self.app.log_t(f"⚠ Error en la captura de teclado: {e}")

    def parar(self):
        try:
            if self.rl is not None:
                CFRunLoopStop(self.rl)
        except Exception:
            pass

    def _cb(self, proxy, tipo, evento, refcon):
        try:
            if tipo in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
                CGEventTapEnable(self.tap, True)
                return evento
            kc = CGEventGetIntegerValueField(evento, kCGKeyboardEventKeycode)
            flags = CGEventGetFlags(evento)
            rep = CGEventGetIntegerValueField(evento, kCGKeyboardEventAutorepeat)
            self.manejar(tipo == kCGEventKeyDown, int(kc), int(flags), bool(rep))
        except Exception as e:
            self.app.log_t(f"⚠ Teclado: {e}")
        return evento

    def liberar_qwerty(self):
        for kc in list(self.held):
            self.held.pop(kc, None)
            self.app.vivo.off(('qw', kc))
        self.app.vivo.pedal(False, "qw")

    def _permitido(self):
        return FOCO_DISPONIBLE and pid_frontal() == os.getpid()

    def manejar(self, abajo, kc, flags, rep):
        app = self.app
        # Espacio controla el transporte mientras hay una canción cargada;
        # en modo de interpretación en vivo queda reservado para el pedal.
        if kc == KEY_SPACE and app.rep.song is not None:
            if abajo and not rep and self._permitido():
                app.rep.play_pause()
            return
        # ---- MidiConnect ----
        if app.f_mc and abajo and not rep and (kc == MC_PREFIX or kc in MC_IDX):
            self._mc(kc)
            return
        # ---- QWERTY ----
        if not app.f_qw:
            return
        if kc == KEY_SPACE:
            if rep:
                return
            if abajo and not self._permitido():
                return
            app.vivo.pedal(abajo, "qw")
            return
        if abajo:
            if rep or kc in self.held or (flags & CMD):
                return
            nota = REV_QWERTY.get((kc, _tipo_mod(flags)))
            if nota is None or not self._permitido():
                return
            self.held[kc] = nota
            app.vivo.on(('qw', kc), nota, 60 if flags & ALT else 100)
        elif kc in self.held:
            self.held.pop(kc, None)
            app.vivo.off(('qw', kc))

    def _mc(self, kc):
        ahora = time.time()
        if kc == MC_PREFIX:
            self.mc_buf, self.mc_t = [], ahora
            return
        if self.mc_buf is None or ahora - self.mc_t > 0.5:
            self.mc_buf = None
            return
        self.mc_buf.append(MC_IDX[kc])
        self.mc_t = ahora
        if len(self.mc_buf) == 4:
            a, b, c, d = self.mc_buf
            self.mc_buf = None
            nota, vel = a * 12 + b, min(127, c * 12 + d)
            if nota == 143:
                abajo = c * 12 + d >= 64
                if not abajo or self._permitido():
                    self.app.vivo.pedal(abajo, "mc")
            elif vel > 0:
                if self._permitido():
                    self.app.vivo.on(('mc', nota), nota, vel)
            else:
                self.app.vivo.off(('mc', nota))


# ============================================================================
# Ajustes
# ============================================================================
AJ_DEF = dict(sf2="Jnsgm.sf2", programa=0, transp=0, vol=100, loop=False, speed=1.0, ventana=3.0,
              midi_in="", canal=0, f_midi=False, f_qw=True, f_qw_global=False, f_mc=False,
              pedal_fijo=False, midi_folder="", last_midi="")


def cargar_ajustes():
    aj = dict(AJ_DEF)
    try:
        with open(ARCH_AJUSTES, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k, v in d.items():
            if k in aj:
                aj[k] = v
    except Exception:
        pass
    return aj


def guardar_ajustes(aj):
    try:
        with open(ARCH_AJUSTES, "w", encoding="utf-8") as f:
            json.dump(aj, f, indent=1)
    except Exception:
        pass


def fmt(seg):
    seg = int(seg)
    return f"{seg // 60}:{seg % 60:02d}"


VIRT_NOMBRE = "Piano Synthesia In"
VIRT_ETQ = f"★ Puerto virtual '{VIRT_NOMBRE}'"


# ============================================================================
# Interfaz
# ============================================================================
class App:
    def __init__(self, root):
        self.root = root
        root.title("Synthesia Piano para macOS")
        root.geometry("1180x760")
        root.minsize(900, 560)

        self.aj = cargar_ajustes()
        self.aj['f_qw_global'] = False
        # La transposición siempre inicia en tono normal; los cambios solo duran esta sesión.
        self.aj['transp'] = 0
        self.logq, self.accionq = queue.Queue(), queue.Queue()
        self.transp = int(self.aj['transp'])
        self.ventana = float(self.aj['ventana'])
        self.f_qw = bool(self.aj['f_qw'])
        self.f_qw_global = False
        self.f_mc = bool(self.aj['f_mc'])

        self.sint = Sintetizador(self.log_t)
        self.vivo = Vivo(self)
        self.rep = Reproductor(self)
        self.tap = TapTeclado(self)
        self.midi_port = None
        self.midi_canal = None
        self.sf2s, self.midis, self.dispositivos = {}, {}, []

        self._estilo()
        self._construir()
        self._aplicar_ajustes()

        self.sint.set_vol(int(self.aj['vol']))
        if self.sint.iniciar():
            self.sint.program(CH_VIVO, int(self.aj['programa']))
            self.rep.speed = float(self.aj['speed'])
        self.vivo.set_pedal_fijo(bool(self.aj['pedal_fijo']))
        self.rep.start()
        self.tap.start()

        self.refrescar_sf2(seleccionar=self.aj['sf2'])
        if not self.sf2s:
            self.log("No encontré .sf2: copia uno junto al script o usa «Importar .sf2…»")
        self.refrescar_midis()
        self.refrescar_midi_in()
        if self.aj['f_midi']:
            self.v_midi.set(True)
            self.toggle_midi()
        ult = self.aj.get('last_midi')
        if ult and os.path.isfile(ult):
            self.cargar_midi_ruta(ult, silencioso=True)

        for tecla, fn in (("<Up>", lambda: self.cambiar_transp(1)),
                          ("<Down>", lambda: self.cambiar_transp(-1)),
                          ("<Left>", lambda: self.cambiar_vol(-5)),
                          ("<Right>", lambda: self.cambiar_vol(5))):
            root.bind_all(tecla, lambda e, fn=fn: (fn(), "break")[1])
        root.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.tick()

    # ---------- estilo ----------
    def _estilo(self):
        s = ttk.Style()
        try:
            # En macOS, Aqua usa los controles y colores nativos del sistema.
            s.theme_use("aqua")
        except tk.TclError:
            # El tema Aqua solo existe en macOS; los otros sistemas conservan su tema nativo.
            pass

    # ---------- construcción ----------
    def _construir(self):
        r = self.root
        pad = dict(padx=4, pady=3)

        self.panel_superior = ttk.Frame(r)
        self.panel_superior.pack(fill="x")
        f1 = ttk.Frame(self.panel_superior)
        f1.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(f1, text="Abrir MIDI…", command=self.abrir_midi).pack(side="left", **pad)
        ttk.Button(f1, text="Quitar MIDI", command=self.quitar_midi).pack(side="left", **pad)
        self.cb_midis = ttk.Combobox(f1, state="readonly", width=28, takefocus=0)
        self.cb_midis.pack(side="left", **pad)
        self.cb_midis.bind("<<ComboboxSelected>>", self.elegir_midi)
        ttk.Button(f1, text="▶ / ⏸", width=7, command=self.rep.play_pause).pack(side="left", **pad)
        ttk.Button(f1, text="■", width=3, command=self.rep.stop).pack(side="left", **pad)
        ttk.Button(f1, text="−10 s", width=6, command=lambda: self.rep.seek(-10)).pack(side="left", **pad)
        ttk.Button(f1, text="+10 s", width=6, command=lambda: self.rep.seek(10)).pack(side="left", **pad)
        self.v_loop = tk.BooleanVar()
        ttk.Checkbutton(f1, text="Loop", variable=self.v_loop, command=self.aplicar_loop,
                        takefocus=0).pack(side="left", **pad)
        ttk.Label(f1, text="Velocidad").pack(side="left", padx=(12, 2))
        self.esc_vel = ttk.Scale(f1, from_=0.25, to=2.0, length=120, takefocus=0,
                                 command=self._cambia_vel)
        self.esc_vel.pack(side="left")
        self.lbl_vel = ttk.Label(f1, text="x1.00", width=6)
        self.lbl_vel.pack(side="left")
        self.lbl_tiempo = ttk.Label(f1, text="0:00 / 0:00", font=("Menlo", 13, "bold"))
        self.lbl_tiempo.pack(side="right", padx=8)
        ttk.Button(f1, text="Solo piano", command=self.alternar_vista).pack(side="right", **pad)

        self.barra = ttk.Progressbar(self.panel_superior, maximum=1000)
        self.barra.pack(fill="x", padx=12, pady=3)
        self.barra.bind("<Button-1>", self._click_barra)

        f2 = ttk.Frame(self.panel_superior)
        f2.pack(fill="x", padx=8)
        ttk.Label(f2, text="SoundFont:").pack(side="left", **pad)
        self.cb_sf = ttk.Combobox(f2, state="readonly", width=34, takefocus=0)
        self.cb_sf.pack(side="left", **pad)
        self.cb_sf.bind("<<ComboboxSelected>>", self.elegir_sf2)
        ttk.Button(f2, text="Importar .sf2…", command=self.importar_sf2).pack(side="left", **pad)
        ttk.Button(f2, text="⟳", width=3, command=lambda: self.refrescar_sf2()).pack(side="left", **pad)
        ttk.Button(f2, text="Carpeta MIDI…", command=self.elegir_carpeta_midi).pack(side="left", padx=(10, 4), pady=3)
        ttk.Label(f2, text="Instrumento (en vivo):").pack(side="left", padx=(14, 2))
        self.cb_prog = ttk.Combobox(f2, state="readonly", width=28, values=GM_VALORES, takefocus=0)
        self.cb_prog.pack(side="left", **pad)
        self.cb_prog.bind("<<ComboboxSelected>>", self.elegir_programa)

        f3 = ttk.Frame(self.panel_superior)
        f3.pack(fill="x", padx=8)
        self.v_midi = tk.BooleanVar()
        ttk.Checkbutton(f3, text="MIDI IN", variable=self.v_midi, command=self.toggle_midi,
                        takefocus=0).pack(side="left", **pad)
        self.cb_disp = ttk.Combobox(f3, state="readonly", width=26, takefocus=0)
        self.cb_disp.pack(side="left", **pad)
        ttk.Button(f3, text="⟳", width=3, command=self.refrescar_midi_in).pack(side="left", **pad)
        self.cb_canal = ttk.Combobox(f3, state="readonly", width=11, takefocus=0,
                                     values=["Todos"] + [f"Canal {i}" for i in range(1, 17)])
        self.cb_canal.current(0)
        self.cb_canal.pack(side="left", **pad)
        self.cb_canal.bind("<<ComboboxSelected>>", self.aplicar_canal)
        self.v_qw = tk.BooleanVar()
        ttk.Checkbutton(f3, text="QWERTY", variable=self.v_qw, command=self.aplicar_flags,
                        takefocus=0).pack(side="left", padx=(14, 2))
        self.v_mc = tk.BooleanVar()
        ttk.Checkbutton(f3, text="MidiConnect", variable=self.v_mc, command=self.aplicar_flags,
                        takefocus=0).pack(side="left", padx=(14, 2))
        self.v_pedal_fijo = tk.BooleanVar()
        ttk.Checkbutton(f3, text="Pedal fijo", variable=self.v_pedal_fijo,
                        command=self.toggle_pedal_fijo, takefocus=0).pack(side="left", padx=(14, 2))

        f4 = ttk.Frame(self.panel_superior)
        f4.pack(fill="x", padx=8)
        ttk.Label(f4, text="Transposición (↑↓):").pack(side="left", **pad)
        ttk.Button(f4, text="−", width=3, command=lambda: self.cambiar_transp(-1)).pack(side="left")
        self.lbl_transp = ttk.Label(f4, text="+0", width=5, anchor="center", font=("Menlo", 13, "bold"))
        self.lbl_transp.pack(side="left")
        ttk.Button(f4, text="+", width=3, command=lambda: self.cambiar_transp(1)).pack(side="left")
        ttk.Button(f4, text="0", width=3, command=lambda: self.cambiar_transp(0, reset=True)).pack(side="left", padx=4)
        ttk.Label(f4, text="semitonos").pack(side="left")
        ttk.Label(f4, text="     Volumen (←→):").pack(side="left", **pad)
        ttk.Button(f4, text="−", width=3, command=lambda: self.cambiar_vol(-5)).pack(side="left")
        self.esc_vol = ttk.Scale(f4, from_=0, to=200, length=150, takefocus=0, command=self._cambia_vol)
        self.esc_vol.pack(side="left", padx=4)
        ttk.Button(f4, text="+", width=3, command=lambda: self.cambiar_vol(5)).pack(side="left")
        self.lbl_vol = ttk.Label(f4, text="100%", width=6)
        self.lbl_vol.pack(side="left")
        ttk.Label(f4, text="   Caída (s):").pack(side="left", **pad)
        self.esc_ven = ttk.Scale(f4, from_=1.0, to=8.0, length=100, takefocus=0, command=self._cambia_ven)
        self.esc_ven.pack(side="left")

        self.cv = tk.Canvas(r, bg=BG, highlightthickness=0)
        self.cv.pack(fill="both", expand=True, padx=8, pady=6)
        self.cv.bind("<Button-1>", lambda e: self.cv.focus_set())
        self.btn_mostrar_controles = ttk.Button(r, text="Mostrar controles", command=self.alternar_vista)

    def alternar_vista(self):
        if self.panel_superior.winfo_manager():
            self.panel_superior.pack_forget()
            self.btn_mostrar_controles.place(relx=1.0, x=-16, y=12, anchor="ne")
        else:
            self.btn_mostrar_controles.place_forget()
            self.panel_superior.pack(fill="x", before=self.cv)

    def quitar_midi(self):
        with self.rep.lock:
            self.rep.stop()
            self.rep.song = None
        self.cb_midis.set("")
        self.aj['last_midi'] = ""
        self.log("[MIDI] Canción retirada; entradas en vivo siguen activas")

    def _aplicar_ajustes(self):
        a = self.aj
        self.v_loop.set(bool(a['loop']))
        self.rep.loop = bool(a['loop'])
        self.esc_vel.set(a['speed'])
        self.lbl_vel.config(text=f"x{float(a['speed']):.2f}")
        self.esc_vol.set(a['vol'])
        self.lbl_vol.config(text=f"{int(a['vol'])}%")
        self.esc_ven.set(a['ventana'])
        self.lbl_transp.config(text=f"{self.transp:+d}")
        self.cb_prog.current(max(0, min(127, int(a['programa']))))
        self.v_qw.set(self.f_qw)
        self.v_mc.set(self.f_mc)
        self.v_pedal_fijo.set(bool(a['pedal_fijo']))
        if a['canal']:
            self.cb_canal.current(int(a['canal']))
            self.aplicar_canal()

    # ---------- utilidades ----------
    def log_t(self, msg):
        self.logq.put(msg)

    def log(self, msg):
        hora = time.strftime("%H:%M:%S")
        print(f"[{hora}] {msg}")

    # ---------- SoundFonts ----------
    def buscar_sf2(self):
        res = {}
        for carpeta in (BASE, DIR_SF2):
            for p in glob.glob(os.path.join(carpeta, "*")):
                if p.lower().endswith((".sf2", ".sf3")):
                    res[os.path.basename(p)] = p
        return res

    def refrescar_sf2(self, seleccionar=None):
        self.sf2s = self.buscar_sf2()
        nombres = sorted(self.sf2s, key=str.lower)
        self.cb_sf.config(values=nombres)
        if not nombres:
            return
        elegido = seleccionar if seleccionar in self.sf2s else (
            self.cb_sf.get() if self.cb_sf.get() in self.sf2s else
            ("Jnsgm.sf2" if "Jnsgm.sf2" in self.sf2s else nombres[0]))
        if seleccionar is not None or not self.cb_sf.get():
            self.cb_sf.set(elegido)
            self.cargar_sf2_async(elegido)

    def elegir_sf2(self, _=None):
        self.cargar_sf2_async(self.cb_sf.get())
        self.cv.focus_set()

    def cargar_sf2_async(self, nombre):
        ruta = self.sf2s.get(nombre)
        if not ruta:
            return
        self.aj['sf2'] = nombre

        def tarea():
            self.log_t(f"[SF2] Cargando {nombre}…")
            ok = self.sint.cargar_sf2(ruta)
            self.log_t(f"[SF2] {'Listo: ' if ok else 'Error con '}{nombre}")
        threading.Thread(target=tarea, daemon=True).start()

    def importar_sf2(self):
        rutas = filedialog.askopenfilenames(title="Importar SoundFont",
                                            filetypes=[("SoundFont", "*.sf2 *.sf3 *.SF2 *.SF3")])
        if not rutas:
            return

        def tarea():
            ultimo = None
            for ruta in rutas:
                destino = os.path.join(DIR_SF2, os.path.basename(ruta))
                try:
                    if os.path.abspath(ruta) != os.path.abspath(destino) and \
                            os.path.dirname(os.path.abspath(ruta)) != BASE:
                        self.log_t(f"[SF2] Copiando {os.path.basename(ruta)}…")
                        shutil.copy2(ruta, destino)
                    ultimo = os.path.basename(ruta)
                except Exception as e:
                    self.log_t(f"⚠ No pude copiar {ruta}: {e}")
            if ultimo:
                self.accionq.put(("sf2_nuevo", ultimo))
        threading.Thread(target=tarea, daemon=True).start()

    def elegir_programa(self, _=None):
        p = self.cb_prog.current()
        self.aj['programa'] = p
        self.sint.program(CH_VIVO, p)
        self.cv.focus_set()

    # ---------- MIDI de archivo ----------
    def refrescar_midis(self):
        self.midis = {}
        carpetas = [BASE, DIR_MIDI]
        carpeta_personal = self.aj.get('midi_folder', '')
        if carpeta_personal and os.path.isdir(carpeta_personal):
            carpetas.append(carpeta_personal)
        for carpeta in carpetas:
            for p in glob.glob(os.path.join(carpeta, "*")):
                if p.lower().endswith((".mid", ".midi")):
                    self.midis[os.path.basename(p)] = p
        self.cb_midis.config(values=sorted(self.midis, key=str.lower))

    def elegir_carpeta_midi(self):
        inicial = self.aj.get('midi_folder') or DIR_MIDI
        carpeta = filedialog.askdirectory(title="Elige la carpeta con archivos MIDI",
                                          initialdir=inicial if os.path.isdir(inicial) else BASE)
        if not carpeta:
            return
        self.aj['midi_folder'] = carpeta
        self.refrescar_midis()
        self.log(f"[MIDI] Carpeta añadida: {carpeta}")

    def abrir_midi(self):
        ruta = filedialog.askopenfilename(title="Elige un archivo MIDI",
                                          filetypes=[("MIDI", "*.mid *.midi *.MID *.MIDI")])
        if ruta:
            self.cargar_midi_ruta(ruta)

    def elegir_midi(self, _=None):
        ruta = self.midis.get(self.cb_midis.get())
        if ruta:
            self.cargar_midi_ruta(ruta)
        self.cv.focus_set()

    def cargar_midi_ruta(self, ruta, silencioso=False):
        try:
            c = cargar_midi(ruta)
        except Exception as e:
            if not silencioso:
                messagebox.showerror("Error", f"No pude leer el archivo:\n{e}")
            self.log(f"⚠ No pude leer {os.path.basename(ruta)}: {e}")
            return
        self.rep.cargar(c)
        self.aj['last_midi'] = ruta
        self.cb_midis.set(c.nombre)
        self.log(f"[MIDI] Cargado: {c.nombre} ({len(c.notas)} notas, {fmt(c.duracion)})")

    def aplicar_loop(self):
        self.rep.loop = self.v_loop.get()
        self.aj['loop'] = self.rep.loop

    def _cambia_vel(self, v):
        v = round(float(v), 2)
        self.rep.set_speed(v)
        self.aj['speed'] = v
        self.lbl_vel.config(text=f"x{v:.2f}")

    def _click_barra(self, e):
        c = self.rep.song
        if c and c.duracion:
            self.rep.seek_abs(c.duracion * e.x / max(1, self.barra.winfo_width()))

    # ---------- entradas ----------
    def aplicar_flags(self):
        self.f_qw = self.v_qw.get()
        self.f_qw_global = False
        self.f_mc = self.v_mc.get()
        self.aj.update(f_qw=self.f_qw, f_qw_global=self.f_qw_global, f_mc=self.f_mc)
        if not self.f_qw:
            self.tap.liberar_qwerty()
        if not self.f_mc:
            self.vivo.soltar_todo('mc')
            self.vivo.pedal(False, "mc")

    def toggle_pedal_fijo(self):
        abajo = self.v_pedal_fijo.get()
        self.aj['pedal_fijo'] = abajo
        self.vivo.set_pedal_fijo(abajo)

    def refrescar_midi_in(self):
        try:
            nombres = list(mido.get_input_names())
        except Exception as e:
            nombres = []
            self.log(f"⚠ No puedo listar dispositivos MIDI ({e}). ¿python-rtmidi instalado?")
        self.dispositivos = [VIRT_ETQ] + [n for n in nombres if n != VIRT_NOMBRE]
        self.cb_disp.config(values=self.dispositivos)
        pref = self.aj.get('midi_in')
        self.cb_disp.set(pref if pref in self.dispositivos else self.dispositivos[0])

    def aplicar_canal(self, _=None):
        i = self.cb_canal.current()
        self.midi_canal = None if i <= 0 else i - 1
        self.aj['canal'] = i
        self.vivo.soltar_todo('midi')
        self.cv.focus_set()

    def toggle_midi(self):
        self.vivo.soltar_todo('midi')
        self.vivo.pedal(False, "midi")
        if self.midi_port is not None:
            try:
                self.midi_port.close()
            except Exception:
                pass
            self.midi_port = None
        self.aj['f_midi'] = self.v_midi.get()
        if not self.v_midi.get():
            self.log("[MIDI IN] desconectado")
            return
        nombre = self.cb_disp.get()
        try:
            if nombre == VIRT_ETQ:
                self.midi_port = mido.open_input(VIRT_NOMBRE, virtual=True, callback=self._cb_midi)
            else:
                self.midi_port = mido.open_input(nombre, callback=self._cb_midi)
            self.aj['midi_in'] = nombre
            self.log(f"[MIDI IN] Conectado: {nombre}")
        except Exception as e:
            self.v_midi.set(False)
            self.aj['f_midi'] = False
            self.log(f"⚠ No pude abrir '{nombre}': {e}")

    def _cb_midi(self, msg):
        try:
            ch = getattr(msg, 'channel', None)
            if self.midi_canal is not None and ch is not None and ch != self.midi_canal:
                return
            if msg.type == 'note_on' and msg.velocity > 0:
                self.vivo.on(('midi', ch, msg.note), msg.note, msg.velocity)
            elif msg.type in ('note_off', 'note_on'):
                self.vivo.off(('midi', ch, msg.note))
            elif msg.type == 'control_change' and msg.control == 64:
                self.vivo.pedal(msg.value >= 64, "midi")
            elif msg.type == 'pitchwheel':
                self.sint.bend(CH_VIVO, msg.pitch)
        except Exception as e:
            self.log_t(f"⚠ MIDI IN: {e}")

    # ---------- transposición / volumen ----------
    def cambiar_transp(self, d, reset=False):
        self.transp = 0 if reset else max(-36, min(36, self.transp + d))
        self.aj['transp'] = self.transp
        self.lbl_transp.config(text=f"{self.transp:+d}")

    def cambiar_vol(self, d):
        v = max(0, min(200, int(self.aj['vol']) + d))
        self.esc_vol.set(v)

    def _cambia_vol(self, valor):
        v = int(round(float(valor)))
        self.aj['vol'] = v
        self.sint.set_vol(v)
        self.lbl_vol.config(text=f"{v}%")

    def _cambia_ven(self, valor):
        self.ventana = round(float(valor), 1)
        self.aj['ventana'] = self.ventana

    # ---------- dibujo estilo Synthesia ----------
    def dibujar(self):
        c = self.cv
        W, H = c.winfo_width(), c.winfo_height()
        if W < 100 or H < 120:
            return
        c.delete("all")
        kh = max(70, int(H * 0.17))
        yk = H - kh
        ww = W / float(N_BLANCAS)
        alto_min_nota = ww / 8  # un octavo del ancho de una tecla blanca
        bh = kh * 0.62
        vent = self.ventana
        ahora = time.perf_counter()
        act = {}

        for n in range(21, 109):
            x0, x1, neg = GEOM[n]
            if not neg and n % 12 in (0, 5):
                c.create_line(x0 * ww, 0, x0 * ww, yk, fill="#262a33")

        s = self.rep.song
        if s:
            pos = self.rep.get_pos()
            sonando = self.rep.estado == 'playing'
            tr = self.transp
            lo = bisect.bisect_left(s.inicios, pos - s.maxdur)
            hi = bisect.bisect_right(s.inicios, pos + vent)
            for i in range(lo, hi):
                ini, fin, nota, ch, vel = s.notas[i]
                if fin < pos or ch == 9:
                    continue
                n = nota + tr
                if n < 21 or n > 108:
                    continue
                x0, x1, neg = GEOM[n]
                yb = yk - (max(ini, pos) - pos) / vent * yk
                yt = yk - (min(fin, pos + vent) - pos) / vent * yk
                if yb - 1 - yt < alto_min_nota:
                    yt = yb - 1 - alto_min_nota
                col = PALETA[ch % len(PALETA)]
                c.create_rectangle(x0 * ww + 1, yt, x1 * ww - 1, yb - 1, fill=col, outline="#000")
                if sonando and ini <= pos <= fin:
                    act[n] = col

        pxs = yk / vent
        with self.vivo.lock:
            vis = list(self.vivo.vis)
        for v in vis:
            n = v['n']
            if n < 21 or n > 108:
                continue
            yb = yk - ((ahora - v['t1']) * pxs if v['t1'] is not None else 0)
            yt = max(0, yk - (ahora - v['t0']) * pxs)
            if yb < 0:
                continue
            if yb - 1 - yt < alto_min_nota:
                yt = yb - 1 - alto_min_nota
            x0, x1, neg = GEOM[n]
            c.create_rectangle(x0 * ww + 1, yt, x1 * ww - 1, yb - 1, fill=ORO, outline="#000")
            if v['t1'] is None:
                act[n] = ORO
        self.vivo.recortar(ahora, vent)

        # teclado
        for n in range(21, 109):
            x0, x1, neg = GEOM[n]
            if neg:
                continue
            c.create_rectangle(x0 * ww, yk, x1 * ww, H, fill=act.get(n, "#f4f4f4"), outline="#555")
            if n % 12 == 0:
                c.create_text((x0 + 0.5) * ww, H - 10, text=f"C{n // 12 - 1}", fill="#555",
                              font=("Helvetica", 8))
        for n in range(21, 109):
            x0, x1, neg = GEOM[n]
            if not neg:
                continue
            c.create_rectangle(x0 * ww, yk, x1 * ww, yk + bh, fill=act.get(n, "#161616"), outline="#000")
        c.create_line(0, yk, W, yk, fill="#c0392b", width=2)

    # ---------- bucle de UI ----------
    def tick(self):
        while True:
            try:
                self.log(self.logq.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                acc = self.accionq.get_nowait()
            except queue.Empty:
                break
            if acc[0] == "sf2_nuevo":
                self.refrescar_sf2(seleccionar=acc[1])

        dur = self.rep.song.duracion if self.rep.song else 0.0
        pos = self.rep.get_pos()
        self.lbl_tiempo.config(text=f"{fmt(pos)} / {fmt(dur)}")
        self.barra["value"] = (pos / dur * 1000) if dur else 0
        self.dibujar()
        self.root.after(20, self.tick)

    def cerrar(self):
        guardar_ajustes(self.aj)
        self.rep._quit = True
        self.rep.stop()
        self.vivo.soltar_todo()
        self.tap.parar()
        if self.midi_port is not None:
            try:
                self.midi_port.close()
            except Exception:
                pass
        self.sint.cerrar()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
