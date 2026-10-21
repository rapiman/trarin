# Formato de set de Ableton Live (.als) — lo que usa trarin

XML en UTF-8 comprimido con gzip. Verificado con sets guardados por Live 12.1 y 12.4 (incluido un set
"Live 11" reguardado por 12). Referencias: observación directa y
[ableton-als](https://github.com/kevinkirsten/ableton-als) (MIT).

## Cabecera

`<Ableton MajorVersion="5" MinorVersion="12.0_12402" SchemaChangeCount="1" Creator="Ableton Live 12.4d1" Revision="…">`.
`MajorVersion` vale 5 desde Live 9; la versión real es el prefijo de `MinorVersion`.

## Estructura usada

| Dato | Ruta (desde `LiveSet`) |
|---|---|
| Pistas | `Tracks/{AudioTrack,MidiTrack,ReturnTrack,GroupTrack}` (atributo `Id`; `TrackGroupId` = grupo) |
| Pista principal | `MainTrack` (Live 12) o `MasterTrack` (anteriores) |
| Tempo | `MainTrack/DeviceChain/Mixer/Tempo/Manual` |
| Compás | `…/Mixer/TimeSignature/Manual`, como id = 99·log2(denominador) + numerador − 1 (4/4 = 201) |
| Mapa de tempo y compás | `MainTrack/AutomationEnvelopes/Envelopes/AutomationEnvelope`: `EnvelopeTarget/PointeeId` = `Id` del `AutomationTarget` del parámetro; eventos `FloatEvent`/`EnumEvent` con `Time` en negras; el valor inicial va en `Time="-63072000"` |
| Marcadores | `Locators/Locators/Locator` (`Id`, `Time` en negras, `Name`) |
| Escenas | `Scenes/Scene/Name` |
| Mezcla | `DeviceChain/Mixer`: `Volume/Manual` (lineal, 1 = 0 dB), `Pan/Manual` (−1 a 1), `Speaker/Manual` (true = suena), `Sends/TrackSendHolder/Send/Manual` (uno por pista de retorno) |
| Ruteo | `DeviceChain/AudioOutputRouting/UpperDisplayString`, entradas en `AudioInputRouting`/`MidiInputRouting` |
| Dispositivos | `DeviceChain/DeviceChain/Devices/*` (`PluginDevice/PluginDesc/{Vst,Vst3}PluginInfo` para plugins) |
| Clips del Arrangement | audio: `DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip`; MIDI: `…/ClipTimeable/ArrangerAutomation/Events/MidiClip`. `CurrentStart`/`CurrentEnd` en negras, `Loop/LoopStart` = inicio del contenido |
| Clips de Session | `DeviceChain/MainSequencer/ClipSlotList/ClipSlot/ClipSlot/Value/{AudioClip,MidiClip}` (un slot por escena) |
| Notas | `Notes/KeyTracks/KeyTrack` (`MidiKey`) → `Notes/MidiNoteEvent` (`Time`, `Duration`, `Velocity`, `NoteId`…), tiempos relativos al contenido del clip |
| Audio | `SampleRef/FileRef/Path` (absoluta) y `RelativePath`; `WarpMarkers/WarpMarker` (`SecTime`, `BeatTime`) |

## Escritura (`trarin.ableton.escribir`)

Se edita una copia del set original: solo se cambian los valores que el JSON modificó (tempo, compás,
marcadores, nombre/volumen/paneo/mute de pistas, posición, nombre y notas de clips del Arrangement).
Sin cambios, el XML resultante es equivalente al original. Reglas respetadas: los `Id` de lista son
locales a su lista; los marcadores se ordenan por tiempo; al reconstruir notas se actualiza
`NoteIdGenerator/NextId`.

Verificado: un set del demo de Live 12 con tempo, marcadores, volumen, un clip movido y notas
transpuestas abrió en Ableton Live 12.4.5 sin errores (registro de Live y título de la ventana).

## Pendiente

Crear pistas o clips nuevos (requiere clonar bloques y renumerar `AutomationTarget`/`NextPointeeId`),
automatización de pistas al escribir, parámetros de dispositivos, warp al escribir, Live 10 y anteriores.
