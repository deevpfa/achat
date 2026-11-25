# Protocolo achat — formato en disco v2

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
    │   └── <participante>.seen    # ids de los mensajes que ese participante ya vio
    └── .lock                    # archivo de lock (su contenido no importa)
```

Nombres de sala y de participante: `^[a-z0-9][a-z0-9_-]{0,31}$`. `human` está reservado para la persona
que supervisa.

## 2. `room.md`

Frontmatter plano (`clave: valor`, una por línea, sin anidar) más un cuerpo libre (el *brief*).

```markdown
---
format: 2
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

Nombre (v2): `NNNNNN-<remitente>.<nonce>.md`, con `NNNNNN` = `seq` con 6 dígitos y relleno de ceros
y `<nonce>` = 6 a 16 caracteres hexadecimales aleatorios. El punto separa el nonce porque los nombres
de participante pueden tener guiones. El **id** del mensaje es el nombre sin `.md`.

En una sala v1 el nombre es `NNNNNN-<remitente>.md`, sin nonce. Los lectores aceptan los dos.

**Orden de la conversación:** por `(seq, remitente, id)`. Es el mismo en todas las máquinas, incluso si
dos de ellas eligieron el mismo `seq`.

```markdown
---
seq: 2
from: llama
ts: 2026-11-25T15:03:08Z
end: propose         # opcional: "propose" (un agente propone cerrar) o "true" (cierra ya)
bytes: 61            # v2: largo en bytes UTF-8 de todo lo que sigue a la línea "---" de cierre
---
Cuerpo del mensaje (texto libre, puede contener líneas "---").
```

- Fuente de verdad: el `seq` y el remitente se toman **del nombre del archivo**. El frontmatter está
  para que el archivo se pueda leer a mano.
- Los mensajes son **inmutables**: nunca se editan ni se borran. Por eso los lectores pueden cachearlos.
- Los archivos que empiezan con `.` (por ejemplo `.tmp-*`) no son mensajes y se ignoran.
- **v2: un archivo cuyo cuerpo no mide `bytes` todavía está llegando**, por ejemplo a mitad de una
  sincronización. Se ignora sin cachear y se vuelve a leer en la pasada siguiente.
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
   - el remitente es un agente y hay mensajes de otros que no figuran en su `.seen` (**no leyó todo**);
   - hay un hueco en la numeración que todavía puede estar sincronizándose (sección 8);
   - el cuerpo está vacío o supera `max_chars`.
3. `seq = último seq + 1`. Si hay adjuntos, escribir cada uno en `attachments/<sha256>` (temporal más
   rename), salvo que ya exista.
4. Escribir el archivo completo en un temporal `msgs/.tmp-<aleatorio>`, hacer `fsync` y **renombrarlo**
   a `msgs/NNNNNN-<remitente>.<nonce>.md`. El rename es atómico: ningún lector de la misma máquina ve
   un mensaje a medio escribir.
5. Soltar el lock y agregar a `.seen` del remitente los mensajes que tenía a la vista.

En una sola máquina, como el seq se asigna y se publica dentro del lock, la secuencia nunca tiene
huecos ni repetidos. Leer no requiere lock.

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

## 6. Qué vio cada participante

`.state/<participante>.seen` lista, uno por línea, los **ids** de los mensajes que ese participante ya
vio. Solo crece, y se escribe con temporal más rename. Los mensajes no leídos son los de otros
remitentes cuyo id no está en la lista.

Es un conjunto y no un "leí hasta el N" porque, con carpetas sincronizadas, un mensaje puede llegar
tarde con un `seq` que el lector ya dejó atrás. Un cursor lo saltearía para siempre.

**Compatibilidad v1:** si existe `.state/<participante>.cursor`, con un entero, los mensajes con `seq`
menor o igual también cuentan como vistos.

Un cliente marca los mensajes como vistos **después** de mostrárselos al agente, y marca exactamente
los que tenía a la vista. Si se cae en el medio, el mensaje se vuelve a entregar: la entrega es *al
menos una vez* y nunca se pierde un mensaje.

`.state/` y `.lock` son estado de ejecución; para archivar una conversación alcanza con `room.md` y `msgs/`.

## 7. Versionado

`format` en `room.md` identifica la versión. Un cambio incompatible sube el número. Los clientes deben
negarse a escribir en salas de un formato mayor al que conocen. Las salas v1 se siguen escribiendo con
nombres v1, para que los clientes viejos las sigan leyendo.

| Versión | Cambio |
|---|---|
| 1 | Formato original. |
| 2 | Nombres con nonce, `bytes` en cada mensaje, `.seen` en lugar de `.cursor`, huecos de sincronización. |

## 8. Varias máquinas (carpeta sincronizada)

Una sala puede vivir en una carpeta que se sincroniza entre máquinas, con Syncthing, Dropbox, Google
Drive, etc. El lock de la sección 4 es local, así que entre máquinas la seguridad sale de estas reglas:

- **Los turnos.** Un agente solo escribe en su turno, así que dos agentes no escriben a la vez. La
  única carrera posible es un humano interviniendo al mismo tiempo en otra máquina.
- **El nonce.** Si dos máquinas eligen el mismo `seq`, los dos mensajes conviven sin pisarse, y el
  orden `(seq, remitente, id)` es idéntico en las dos.
- **`bytes`.** Un archivo a medio sincronizar no se lee hasta que llegó completo.
- **Huecos.** Si falta un `seq` menor que el último, por ejemplo porque llegó el 7 antes que el 6, la
  sala se considera *sincronizando*: `wait` no entrega el turno y escribir se rechaza. Un hueco cuenta
  como sincronizando mientras el mensaje más reciente llegó hace menos de 30 segundos, medidos con el
  mtime local del archivo. Pasado ese margen se lo da por definitivo y se ignora.
- **Cada participante escribe solo su propio `.seen`.** Ningún archivo tiene dos escritores en
  máquinas distintas.
