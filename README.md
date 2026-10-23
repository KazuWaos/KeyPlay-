# KeyPlay++

KeyPlay++ es un piano virtual para macOS con reproducción visual de notas estilo Synthesia. Reproduce archivos MIDI con sonido de piano y otros instrumentos, y permite tocar en vivo desde un teclado MIDI o desde el teclado del Mac.

## Descargar e instalar

1. Descarga **`KeyPlay++.dmg`** desde la sección **Releases** de este repositorio.
2. Abre el DMG y arrastra **KeyPlay++** a **Aplicaciones**.
3. Abre KeyPlay++ desde Aplicaciones.

Si macOS muestra una advertencia de seguridad, confirma que descargaste el DMG desde este repositorio y sigue las opciones de apertura que ofrece macOS.

## Qué incluye

- Reproductor de archivos `.mid` y `.midi` con pausa, parada, búsqueda, loop y control de velocidad.
- Visualización de notas descendentes sobre un teclado de 88 teclas.
- SoundFont `Jnsgm.sf2` incluido como sonido predeterminado.
- Selección de otros SoundFonts e instrumentos para tocar en vivo.
- Entrada en vivo desde dispositivos MIDI físicos y puertos MIDI virtuales.
- Modo de teclado QWERTY y compatibilidad con el protocolo MidiConnect de MIDI++.
- Transposición, volumen, sustain fijo y modo **Solo piano**.
- Selector para añadir una carpeta personal con archivos MIDI.

## Uso rápido

### Reproducir un archivo MIDI

1. Pulsa **Abrir MIDI…** o elige una canción del selector.
2. Pulsa **▶ / ⏸** para reproducir o pausar.
3. Usa **■** para detener, **−10 s** y **+10 s** para buscar, y **Loop** para repetir la canción.
4. Pulsa **Quitar MIDI** para retirar la canción y dejar la app lista para tocar en vivo.

Para añadir una carpeta con tus canciones, pulsa **Carpeta MIDI…**. Sus archivos aparecerán en el selector.

### Tocar en vivo

- Activa **MIDI IN** y selecciona el teclado o puerto MIDI conectado.
- Activa **QWERTY** para tocar con las teclas asignadas del teclado del Mac.
- Activa **MidiConnect** para recibir el protocolo de teclado numérico enviado por MIDI++.
- Usa **Pedal fijo** para mantener activado o desactivado el sustain.
- El selector de **Instrumento (en vivo)** cambia el sonido de las notas en vivo.

La entrada MIDI física puede seguir tocando aunque KeyPlay++ no esté al frente. QWERTY y MidiConnect solo funcionan cuando la ventana de KeyPlay++ está al frente. La reproducción de una canción MIDI continúa en segundo plano.

## Controles de teclado

Con la ventana de KeyPlay++ al frente:

| Tecla | Acción |
|---|---|
| Espacio | Con una canción cargada: reproducir o pausar. Sin canción y con QWERTY activo: pedal sustain. |
| ↑ / ↓ | Transponer un semitono hacia arriba o abajo |
| ← / → | Bajar o subir el volumen en 5 % |

## Permisos de macOS

Para tocar con QWERTY, KeyPlay++ necesita permiso para recibir las pulsaciones del teclado incluso cuando otra app está activa. Ve a **Ajustes del Sistema → Privacidad y seguridad → Monitorización de entrada** y permite KeyPlay++. Si macOS también solicita permiso en **Accesibilidad**, permite KeyPlay++ allí y vuelve a abrir la app. Sin este permiso, QWERTY no podrá recibir las teclas.

## Requisitos y ayuda

KeyPlay++ necesita FluidSynth para generar audio. Si no se escucha, instala FluidSynth con Homebrew:

```bash
brew install fluid-synth
```

Si el teclado MIDI no aparece, comprueba que esté conectado y vuelve a actualizar la lista de dispositivos desde la app. Los archivos MIDI de tipo 2 no están soportados.
