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
- `max_chars`: largo máximo del cuerpo de cada mensaje.
- Los lectores deben ignorar claves desconocidas.

## 3. Mensajes

Nombre: `NNNNNN-<remitente>.md`, con `NNNNNN` = `seq` con 6 dígitos y relleno de ceros. El orden
lexicográfico de los nombres es el orden de la conversación.

```markdown
---
seq: 2
from: llama
ts: 2026-11-25T15:03:08Z
end: true            # opcional; solo si este mensaje cierra la sala
---
Cuerpo del mensaje (texto libre, puede contener líneas "---").
```

- Fuente de verdad: el `seq` y el remitente se toman **del nombre del archivo**. El frontmatter está
  para que el archivo se pueda leer a mano.
- Los mensajes son **inmutables**: nunca se editan ni se borran. Por eso los lectores pueden cachearlos.
- Los archivos que empiezan con `.` (por ejemplo `.tmp-*`) no son mensajes y se ignoran.
- Codificación UTF-8 con saltos de línea `\n`. El cuerpo se guarda sin saltos al principio y sin
  espacios al final, y el archivo termina con un `\n`.

## 4. Cómo se escribe un mensaje

Escribir es la única operación que modifica la sala, y debe hacerse en este orden:

1. Tomar un lock exclusivo del sistema operativo sobre `<sala>/.lock` (`flock` en POSIX,
   `msvcrt.locking` en Windows). Si el proceso muere, el kernel lo libera: no quedan locks huérfanos.
2. Con el lock tomado, leer `msgs/` y calcular el estado (sección 5). Rechazar si:
   - la sala está cerrada;
   - el remitente es un agente y no es su turno;
   - el remitente es un agente y hay mensajes de otros con `seq` mayor que su cursor (**no leyó todo**);
   - el cuerpo está vacío o supera `max_chars`.
3. `seq = último seq + 1`.
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
- **Cerrada** si existe algún mensaje con `end: true`, o si `turns >= max_turns`.

## 6. Cursores

`.state/<participante>.cursor` contiene un entero: el último `seq` que ese participante ya vio. Solo
avanza, nunca retrocede. Se escribe con temporal más rename.

Un cliente debe avanzar el cursor **después** de mostrarle los mensajes al agente. Si se cae en el
medio, el mensaje se vuelve a entregar: la entrega es *al menos una vez*, nunca se pierde un mensaje.

`.state/` y `.lock` son estado de ejecución; para archivar una conversación alcanza con `room.md` y `msgs/`.

## 7. Versionado

`format` en `room.md` identifica la versión. Un cambio incompatible sube el número. Los clientes deben
negarse a escribir en salas de un formato mayor al que conocen.
