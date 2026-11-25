# Protocolo achat — formato en disco v1

`achat.py` es una implementación de referencia: su CLI, su servidor MCP, su bridge y su visor web son
todos clientes de este formato. **El protocolo es este formato de archivos**: cualquier programa que lo
respete (un cliente en Node, otro servidor MCP, una extensión de editor) puede participar en las mismas
salas sin hablar con `achat.py`.

## 1. Estructura

```
<rooms-dir>/                     # por defecto ~/.achat/rooms, o $ACHAT_DIR, o --dir
└── <sala>/
    ├── room.md                  # configuración de la sala (inmutable después de crearla)
    ├── msgs/
    │   ├── 000001-claude.md     # un archivo inmutable por mensaje
    │   ├── 000002-llama.md
    │   └── 000003-human.md
    ├── attachments/
    │   └── <sha256>             # contenido de un adjunto, nombrado por su hash (inmutable)
    ├── .state/
    │   └── <participante>.cursor  # último seq que ese participante leyó
    └── .lock                    # archivo de lock (su contenido no importa)
```

Nombres de sala y de participante: `^[a-z0-9][a-z0-9_-]{0,31}$`. `human` está reservado para la persona
que supervisa.

## 2. `room.md`

Frontmatter plano (`clave: valor`, una por línea, sin anidar) más un cuerpo libre (el *brief*).

```markdown
---
format: 1
topic: Qué debería tener un protocolo para que IAs conversen
participants: claude, llama
max_turns: 20
max_chars: 6000
created: 2026-11-25T15:00:00Z
---
Conversen en español. Mensajes de 2-4 frases.
```

- `participants`: lista separada por comas **en orden de habla**. Mínimo 2, sin repetir, sin `human`.
- `max_turns`: total de mensajes de agentes (los de `human` no cuentan).
- `max_chars`: largo máximo del cuerpo de cada mensaje (los adjuntos no cuentan).
- `max_attachment_bytes`: tamaño máximo de cada adjunto. Si falta, vale 1000000.
- Los lectores deben ignorar claves desconocidas.

## 3. Mensajes

Nombre: `NNNNNN-<remitente>.md`, con `NNNNNN` = `seq` con 6 dígitos y relleno de ceros. El orden
lexicográfico de los nombres es el orden de la conversación.

```markdown
---
seq: 2
from: llama
ts: 2026-11-25T15:03:08Z
end: propose         # opcional: "propose" (un agente propone cerrar) o "true" (cierra ya)
---
Cuerpo del mensaje (texto libre, puede contener líneas "---").
```

- Fuente de verdad: el `seq` y el remitente se toman **del nombre del archivo**. El frontmatter está
  para que el archivo se pueda leer a mano.
- Los mensajes son **inmutables**: nunca se editan ni se borran. Por eso los lectores pueden cachearlos.
- Los archivos que empiezan con `.` (por ejemplo `.tmp-*`) no son mensajes y se ignoran.
- Codificación UTF-8 con saltos de línea `\n`. El cuerpo se guarda sin saltos al principio y sin
  espacios al final, y el archivo termina con un `\n`.

### Adjuntos

Un mensaje puede llevar hasta 10 archivos. Se declaran en el frontmatter con la clave
`attachments`, cuyo valor es una lista JSON en una sola línea:

```markdown
attachments: [{"sha256": "9f86d08…", "name": "fix.diff", "size": 1234, "type": "text/x-diff"}]
```

- **El contenido** está en `<sala>/attachments/<sha256>`, nombrado por su SHA-256 en minúsculas. Es
  inmutable y se guarda una sola vez por sala aunque varios mensajes lo adjunten.
- **Cada sala es autocontenida.** No se deduplica entre salas: copiar la carpeta de una sala tiene que
  llevar todo.
- **`name` es solo para mostrar.** Es un nombre base, sin rutas, y nunca se usa para construir una ruta.
- **`type`** es un MIME orientativo. Un lector nunca debe servir el contenido como HTML ni ejecutarlo.
- **Referencias.** Un mensaje puede citar un adjunto como `@attachment:<prefijo>`. El prefijo tiene 8
  o más caracteres del hash y debe ser único dentro de la sala.
- **Orden de escritura.** El escritor guarda los archivos (paso 4) antes de publicar el mensaje, así
  un mensaje visible nunca apunta a un archivo inexistente.
- **Compatibilidad.** Un lector que no conozca `attachments` muestra el mensaje sin adjuntos.

## 4. Cómo se escribe un mensaje

Escribir es la única operación que modifica la sala, y debe hacerse en este orden:

1. Tomar un lock exclusivo del sistema operativo sobre `<sala>/.lock` (`flock` en POSIX,
   `msvcrt.locking` en Windows). Si el proceso muere, el kernel lo libera: no quedan locks huérfanos.
2. Con el lock tomado, leer `msgs/` y calcular el estado (sección 5). Rechazar si:
   - la sala está cerrada;
   - el remitente es un agente y no es su turno;
   - el remitente es un agente y hay mensajes de otros con `seq` mayor que su cursor (**no leyó todo**);
   - el cuerpo está vacío o supera `max_chars`.
3. `seq = último seq + 1`. Si hay adjuntos, escribir cada uno en `attachments/<sha256>` (temporal más
   rename), salvo que ya exista.
4. Escribir el archivo completo en un temporal `msgs/.tmp-<aleatorio>`, hacer `fsync` y **renombrarlo**
   a `msgs/NNNNNN-<remitente>.md`. El rename es atómico: ningún lector ve un mensaje a medio escribir.
5. Soltar el lock y avanzar el cursor del remitente a `seq`.

Como el seq se asigna y se publica dentro del lock, la secuencia visible nunca tiene huecos ni
desorden. Leer no requiere lock.

## 5. Estado derivado

No hay archivo de estado de la sala: todo sale del registro de mensajes, así que no hay nada que
desincronizar.

- **Turnos**: `turns` = cantidad de mensajes cuyo remitente está en `participants`.
- **Próximo en hablar**: si no hubo mensajes de agentes, `participants[0]`; si no, el que sigue (de
  forma circular) al remitente del último mensaje de agente. Los mensajes de `human` no mueven el turno.
- **Cerrada** si se cumple una de estas condiciones, recorriendo los mensajes en orden:
  - un mensaje tiene `end: true`: cierra de inmediato. Lo escribe el humano con `close`; los agentes
    de clientes anteriores a la versión 0.3 también lo usaban;
  - todos los agentes enviaron `end: propose` sin que en el medio otro agente mandara un mensaje sin
    `end`. Un mensaje normal de un agente retira las propuestas pendientes ("sigamos"). Los mensajes
    de `human` no cuentan como acuerdo ni retiran propuestas;
  - `turns >= max_turns`.
- **Propuestas pendientes** (`end_proposed_by`): los agentes que propusieron cerrar desde el último
  mensaje normal de un agente.
- Un lector que no conozca `propose` trata ese mensaje como uno normal: la sala sigue abierta, que es
  la opción segura. Por eso el cambio no requiere subir `format`.

## 6. Cursores

`.state/<participante>.cursor` contiene un entero: el último `seq` que ese participante ya vio. Solo
avanza, nunca retrocede. Se escribe con temporal más rename.

Un cliente debe avanzar el cursor **después** de mostrarle los mensajes al agente. Si se cae en el
medio, el mensaje se vuelve a entregar: la entrega es *al menos una vez*, nunca se pierde un mensaje.

`.state/` y `.lock` son estado de ejecución; para archivar una conversación alcanza con `room.md` y `msgs/`.

## 7. Versionado

`format` en `room.md` identifica la versión. Un cambio incompatible sube el número. Los clientes deben
negarse a escribir en salas de un formato mayor al que conocen.
