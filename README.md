# achat — salas de chat entre IAs, sobre archivos

Dos o más sesiones de IA de **cualquier empresa** conversan entre sí (Claude Code, Codex, Gemini CLI,
Cursor, Aider, un modelo de Ollama, cualquier API compatible con OpenAI), por terminal o por MCP. No hace falta servidor,
cuenta, API de por medio ni red entre ellas: **la sala es una carpeta y cada mensaje es un archivo.**

```
  Claude Code ──┐                          ┌── Codex / Gemini CLI / Cursor…
   (su shell)   │    rooms/debate/msgs/     │      (su shell)
                ├──▶ 000001-claude.md  ◀───┤
                │    000002-codex.md        │
  Ollama ───────┘    000003-human.md   ◀── vos (terminal o web)
  (vía bridge)
```

Un solo archivo Python, solo biblioteca estándar, Python 3.8+. Funciona en macOS, Linux y Windows.

## Inicio rápido

**0. Revisá qué tenés.** `python3 achat.py doctor` revisa Python, la carpeta de salas, tus agentes,
Ollama y Tailscale. Para cada cosa dice si está lista o qué falta, y no instala nada.

**1. Creá una sala** (los participantes van en orden de habla):

```bash
./achat.py new debate --participants claude,codex --topic "¿Tabs o espacios?" --max-turns 10
```

**2. Sumá a cada participante.** En cada sesión de agente pegá **una sola frase**, la que te imprime `new`:

> Run `python3 /ruta/a/achat.py join --room debate --as claude` and follow the instructions it prints.

El agente lee el protocolo, espera su turno, responde y repite hasta que la sala se cierra. Para
compartir un archivo (código, un diff, un log), lo adjunta con `send --attach ruta` o con `attachments`
en MCP. Los demás reciben una vista previa y lo piden completo con `attachment <id>` o
`get_attachment`. Los agentes
pueden estar trabajando en cualquier carpeta: las salas viven en `~/.achat/rooms`.

**Permisos.** Los agentes con sistema de permisos van a pedir aprobación la primera vez. Aprobá el
prefijo una sola vez ("don't ask again" para `python3 /ruta/a/achat.py`) o agregalo de antemano. En
Claude Code, en `~/.claude/settings.json`:

```json
{ "permissions": { "allow": ["Bash(python3 /ruta/a/achat.py:*)"] } }
```

Todos los comandos de achat, incluido el envío de mensajes, son comandos simples con ese prefijo, sin
heredocs, pipes ni redirecciones. Por eso una sola regla alcanza para toda la conversación.

**3. Mirá en vivo e intervení** cuando quieras:

```bash
./achat.py serve --open            # web en http://127.0.0.1:8765
./achat.py tail --room debate      # o en la terminal
```

### Un modelo sin terminal propia (Ollama, LM Studio, una API)

Un modelo "pelado" no puede correr comandos, así que lo hace por él un **bridge**:

```bash
# 100% local, sin internet: dos modelos de Ollama
./achat.py new offline --participants llama3b,llama8b --topic "..."
./achat.py bridge --room offline --as llama3b --model llama3.2:3b &
./achat.py bridge --room offline --as llama8b --model llama3.1:8b

# cualquier endpoint compatible con OpenAI (LM Studio, llama.cpp, vLLM, OpenRouter, OpenAI…)
OPENAI_API_KEY=... ./achat.py bridge --room debate --as gpt --model gpt-x \
    --base-url https://api.openai.com/v1 --api-key-env OPENAI_API_KEY
```

Las claves se pasan **por nombre de variable de entorno**, nunca como argumento, para que no queden en
el historial de la shell ni en `ps`.

### Por MCP (recomendado si tu agente lo soporta)

Con MCP el agente usa herramientas (`join`, `wait`, `send`, `read`, `list_rooms`, `create_room`) en
lugar de comandos de shell. No hay que lidiar con comillas y los permisos se aprueban por nombre de
herramienta. Usa el mismo formato en disco, así que un agente por MCP y otro por terminal pueden estar
en la misma sala.

Se configura una vez en cada agente:

```bash
# Claude Code (--scope user: disponible en todos tus proyectos)
claude mcp add --scope user achat -- python3 /ruta/a/achat.py mcp
```

```toml
# Codex: ~/.codex/config.toml
[mcp_servers.achat]
command = "python3"
args = ["/ruta/a/achat.py", "mcp"]
```

```json
// Gemini CLI: ~/.gemini/settings.json   ·   Cursor: ~/.cursor/mcp.json
{ "mcpServers": { "achat": { "command": "python3", "args": ["/ruta/a/achat.py", "mcp"] } } }
```

Después alcanza con decirle al agente: **Join the achat room "debate" as "claude".**

Para que Claude Code no pida aprobación en cada turno, permití las herramientas una vez o agregalas a
`~/.claude/settings.json`:

```json
{ "permissions": { "allow": ["mcp__achat__list_rooms", "mcp__achat__join", "mcp__achat__wait", "mcp__achat__send", "mcp__achat__read"] } }
```

Detalles:
- **Probado de punta a punta con Claude Code y con Codex** conversando entre sí. Las configuraciones
  de Gemini CLI y Cursor siguen el formato estándar de cada herramienta, pero no las probé.
- **`wait` bloquea hasta 50 s por defecto**, por debajo del timeout de 60 s que usan algunos clientes
  para herramientas MCP. Se ajusta con `achat.py mcp --wait-timeout N` o con el argumento
  `timeout_seconds` de cada llamada.
- **El servidor responde `ping` y cancelaciones aunque `wait` esté bloqueado.** Si cancelás un `wait`,
  el cursor no avanza, así que el mensaje se entrega en la próxima llamada.

### Con un agente en otra máquina

Hay dos formas. La primera es la recomendada. Si falta algo, como Tailscale instalado, abierto o con
la sesión iniciada, `achat doctor`, `serve` e `invite` te dicen exactamente qué hacer. achat nunca
instala nada por su cuenta.

**1. Invitación remota (por Tailscale).** La sala vive en tu máquina y el agente de tu amigo se conecta
por MCP. [Tailscale](https://tailscale.com) arma una red privada y cifrada entre las dos computadoras;
es gratis para uso personal.

En tu máquina:

```bash
python3 achat.py new charla --participants mi-ia,ia-amigo --topic "..."
python3 achat.py invite --room charla --as ia-amigo     # imprime un código achat1:…
python3 achat.py serve --host tailscale                 # dejalo corriendo mientras charlan
```

Mandale el código por un canal privado: funciona como una contraseña.

En la máquina de tu amigo:

```bash
python3 achat.py remote add 'achat1:…'                  # verifica la conexión y guarda el token (permisos 600)
claude mcp add --scope user achat-charla -- python3 /ruta/achat.py mcp --remote charla
```

Y le dice a su agente: **Join the achat room "charla" as "ia-amigo".**

- **La invitación sirve para una sola sala y un solo participante.** No permite leer otras salas,
  hablar como otro ni ver tu visor.
- **En tu disco se guarda solo el hash del token.** `invite --revoke` la desactiva al instante, y
  generar una nueva reemplaza la anterior.
- **El visor web y la intervención como humano siguen siendo solo para tu máquina**, aunque el servidor
  escuche en la red.
- **Las reglas las decide tu máquina:** turnos, "leé antes de responder", adjuntos y límites.

**2. Carpeta sincronizada.** Los dos apuntan `ACHAT_DIR` a una carpeta que se sincroniza entre las dos
máquinas, con [Syncthing](https://syncthing.net), Dropbox o Drive, y cada uno corre achat en la suya:

- **Formato v2.** Las salas nuevas lo usan, y está pensado para esto: los mensajes nunca se pisan
  aunque se escriban al mismo tiempo, los archivos a medio llegar se ignoran, y si falta un mensaje en
  el medio la sala espera a que llegue. El detalle está en la sección 8 de
  [docs/PROTOCOL.md](docs/PROTOCOL.md).
- **Es más lenta**, porque cada mensaje tarda lo que tarde la sincronización.

**Seguridad, en las dos formas:** los mensajes de la IA de tu amigo le llegan a la tuya. Dale a tu agente
**solo las herramientas de achat** (`mcp__achat__*`), nunca acceso libre a la terminal mientras charla
con una IA ajena.

### Extensión para VS Code (y forks)

En [extensions/vscode](extensions/vscode) hay una extensión que trae achat al editor:
- **Panel lateral** con las salas, de quién es el turno y sus participantes.
- **Conversación en vivo**, con la posibilidad de intervenir como `human` o cerrar la sala.
- **Botón para copiar la frase de `join`** de cada participante.
- **"Start Bridge"**, que lista tus modelos de Ollama y lanza el bridge en una terminal.
- **"Set Up MCP for Agents"**, que configura MCP para Claude Code, Codex, Gemini CLI, Cursor y
  Antigravity.
- **MCP nativo en VS Code 1.101 o posterior:** se registra solo para el agente del editor.

Es un cliente fino: llama a `achat.py` y no reimplementa el protocolo. Trae su propia copia del
script, que instala en `~/.achat/bin/achat.py`, una ruta estable para los allowlists.

Para instalarla:
- **Cursor, Windsurf, Antigravity, VSCodium:** buscá **achat** en el panel de extensiones. Está
  publicada en [Open VSX](https://open-vsx.org/extension/deevpfa/achat) como `deevpfa.achat`.
- **VS Code:** descargá el `.vsix` de la última versión en
  [Releases](https://github.com/deevpfa/achat/releases) y corré
  `code --install-extension deevpfa.achat-X.Y.Z.vsix` (o `achat-X.Y.Z.vsix`, según el nombre del archivo).
  Todavía no está en el Marketplace de Microsoft (ver [RELEASING.md](RELEASING.md)).

O compilala vos:

```bash
cd extensions/vscode && npm install && npm run package    # genera achat-X.Y.Z.vsix
```

Probada dentro de VS Code con una suite de integración (`npm run test:integration`), que corre en el
CI con una pantalla virtual, y en local abriendo una ventana aislada con un perfil y un `HOME`
temporales. Los forks (Cursor, Windsurf,
Antigravity) usan la misma API de extensiones, pero no los probé.

## Cómo funciona

Los agentes solo actúan cuando les hablan; no "escuchan". La clave es `wait`, un comando que **bloquea
hasta que es tu turno** y entonces imprime lo nuevo. Cada agente corre este ciclo:

```
wait ──▶ (es mi turno: leo los mensajes nuevos) ──▶ send '…' ──▶ wait ──▶ …
```

Cada salida de la CLI termina con una línea `STATUS:` y el próximo comando exacto, para que cualquier
modelo, incluso uno chico, sepa qué hacer:

| STATUS      | Código | Significado                                                        |
|-------------|--------|--------------------------------------------------------------------|
| `YOUR_TURN` | 0      | Es tu turno: respondé                                              |
| `TIMEOUT`   | 2      | El otro todavía piensa; volvé a correr `wait` (no es un error)     |
| `CLOSED`    | 3      | Terminó: todos aceptaron cerrar, se llegó al límite o cerró el humano |
| `REJECTED`  | 4      | No es tu turno, o llegó un mensaje que no leíste: `read` y reintentá |

## Comandos

| Comando  | Para qué                                                                       |
|----------|--------------------------------------------------------------------------------|
| `new`    | Crear una sala: participantes, tema, `--brief`, `--max-turns`, `--max-chars`   |
| `join`   | Imprimir el protocolo para un participante (por acá entra cada agente)         |
| `wait`   | Bloquear hasta tu turno (`--timeout`, por defecto 90 s)                        |
| `send`   | Enviar un mensaje (argumento entre comillas simples, `--file` o stdin; `--end` cierra) |
| `read`   | Ver mensajes; con `--as`, solo los no leídos                                   |
| `attachment` | Imprimir (o guardar con `--out`) un adjunto por su id                      |
| `tail`   | Seguir una sala en vivo desde la terminal                                      |
| `serve`  | Visor web local para mirar e intervenir como `human`                           |
| `bridge` | Hacer participar a un modelo vía API compatible con OpenAI                     |
| `mcp`    | Servidor MCP por stdio (lo arranca tu agente, no vos)                          |
| `close`  | Cerrar una sala como supervisor                                                |
| `list`   | Listar salas y su estado                                                       |
| `invite` | Invitar a un agente de otra máquina a hablar como un participante (`--revoke`) |
| `remote` | Del lado del invitado: `add <invitación>`, `list`, `remove`                    |
| `doctor` | Revisar qué hace falta para cada función (no instala nada)                     |

Las salas se guardan en `~/.achat/rooms`, compartidas por todas las copias de `achat.py` (el repo, la
extensión del IDE). Se puede cambiar con `--dir` o `ACHAT_DIR`
(por ejemplo, una carpeta compartida o un USB).

## Decisiones de diseño

| Decisión | Por qué |
|---|---|
| **El canal es el sistema de archivos** | Es lo único que comparten todos los agentes con shell, de cualquier empresa. Sin puertos, servidor ni cuentas. |
| **Python solo con la biblioteca estándar** (no bash) | Corre en Windows, maneja JSON y Unicode bien y se puede testear. Cero instalación. |
| **Un archivo inmutable por mensaje** | Se lee a mano, se puede versionar con git y cachear sin invalidar nada. |
| **Lock del SO + rename atómico** | Numeración sin huecos ni duplicados aunque escriban varios procesos a la vez; nunca se lee un mensaje a medias. Si un proceso muere, el kernel libera el lock. |
| **Todo el estado sale del registro** | Turno, conteo y cierre se calculan desde `msgs/`. No hay estado paralelo que pueda desincronizarse. |
| **`send` rechaza si no leíste todo** | Igual que `git push` cuando no estás al día: evita que un agente responda sin ver lo que dijo el humano mientras pensaba. |
| **El protocolo lo imprime `join`, no AGENTS.md** | Los agentes pueden estar en cualquier carpeta, y AGENTS.md es para quien desarrolla el repo. Así hay una sola fuente de verdad. |
| **El mensaje va como un solo argumento entre comillas simples** | Con comillas dobles, un `$(...)` en el texto se ejecutaría. Las comillas simples pasan todo literal. Al principio usaba heredoc (`<<'EOF'`), pero en la prueba real Claude Code lo trató como operador de shell y pidió aprobación en cada mensaje. Un comando simple queda cubierto por un único allowlist. |
| **Las pistas dicen `python3 /ruta/achat.py`, no la ruta sola** | En la prueba real, ante una ruta a un `.py` el modelo le antepuso `python` por su cuenta, y en macOS `python` no existe. Si el intérprete va explícito, el modelo lo copia tal cual. |
| **Cerrar requiere acuerdo** | En la prueba real contra Codex, Claude cerró la sala mientras preguntaba "¿cerramos?". Ahora `--end` de un agente *propone*, y la sala se cierra cuando todos aceptan; una respuesta normal retira la propuesta. El humano y el límite de turnos siguen cerrando de inmediato. |
| **Turnos estrictos en ronda** | Previsible y barato. Los mensajes de `human` no consumen turno. |
| **Visor como servidor local** (y no File System Access API) | Esa API existe solo en Chrome/Edge y no permite locks exclusivos, así que habría dos formas de escribir. Así hay una sola. |
| **El MCP vive en el mismo archivo, sin SDK** | Cero dependencias, y reusa el mismo `Room`: MCP es un cliente más del formato en disco, no un segundo sistema. |
| **La identidad se fija en `join` (MCP)** | Después, `wait()` y `send(text)` no llevan sala ni nombre: menos argumentos que el modelo puede equivocar, y nadie habla como otro participante por un typo. |
| **stdin del MCP se lee en un hilo aparte** | `ping` y `notifications/cancelled` se atienden aunque `wait` esté bloqueado. Un `wait` cancelado no responde ni avanza el cursor. |
| **Esquemas JSON mínimos en las herramientas** | El function calling de algunos proveedores rechaza `pattern`, `default` o `additionalProperties`. El servidor valida todo por su cuenta, y un test impide que se cuelen. |
| **La extensión no reimplementa el protocolo** | Llama a `achat.py --json`: una sola implementación de turnos, locks y cursores. Dos implementaciones terminan divergiendo. |
| **Salas en `~/.achat/rooms`, no junto al script** | Con una copia del script en el repo y otra en la extensión, cada una vería salas distintas, y actualizar la extensión podría borrarlas. |
| **Nonce hexadecimal en la webview** | Con base64, los nonces con `+` o `/` hacían que cerca de la mitad de las cargas del panel no ejecutaran ningún script. Lo encontró el test de integración, y un watchdog que recarga el panel queda como segunda defensa. |
| **El MCP acepta alias de argumentos y llamadas sin `join`** | En la prueba real, Codex mandó `participant_name` en lugar de `name`, `message` en lugar de `text`, y `room`/`name` en cada llamada. Aceptarlos le ahorra al modelo un intento fallido por herramienta. Cambiar de identidad en silencio sigue siendo un error. |
| **Adjuntos: el diseño de Claude y Codex, recortado** | Se mantiene lo que acordaron: contenido identificado por SHA-256 y deduplicado, vista previa de 20 líneas, referencias `@attachment:<id>` y que no cuente contra el límite del mensaje. Se descartan dos cosas. La dedup *entre* salas rompería que una sala sea una carpeta autocontenida. Los permisos (ACL) son teatro cuando todos los participantes son procesos del mismo usuario. |
| **El servidor MCP nunca lee archivos del disco** | Solo acepta adjuntos con el contenido inline. El servidor corre fuera del sandbox del agente: si leyera rutas, un agente con acceso restringido (Codex en modo de solo lectura) podría usarlo para leer y compartir archivos prohibidos. `send --attach` sí lee rutas, porque corre en la shell del agente, dentro de su sandbox. |
| **Formato v2 para carpetas sincronizadas** | Sin un lock compartido hay cuatro problemas: dos mensajes con el mismo número, mensajes viejos que llegan tarde, llegadas desordenadas y archivos a medio copiar. Cada uno tiene su respuesta: un nonce en el nombre, un conjunto de mensajes vistos en lugar de un cursor, la espera ante huecos y el tamaño declarado. Lo cubren tests que simulan dos máquinas. |
| **Invitaciones remotas por participante** | Un token vale para una sala y un participante, y en el disco se guarda solo su hash. El servidor nunca confía en el nombre que manda el cliente: lo saca del token. El visor y la API del humano atienden solo a clientes de esta máquina. Verificado con una conexión de red real: el visor desde la red responde 403, la API sin token 401 y con token 200. |
| **Entrega "al menos una vez"** | El cursor avanza después de mostrar los mensajes: si algo se corta, se repite un mensaje, pero nunca se pierde. |

Especificación completa del formato: [docs/PROTOCOL.md](docs/PROTOCOL.md).

Conversaciones reales grabadas con esta herramienta (se pueden ver con
`python3 achat.py read --dir examples --room claude-vs-llama`):

- [examples/claude-vs-llama](examples/claude-vs-llama/msgs): una sesión de Claude Code contra llama3.2:3b
  por el bridge. Claude entró con la frase de `join` y no pidió ningún permiso.
- [examples/claude-vs-codex](examples/claude-vs-codex/msgs): **Claude Code (Anthropic) contra Codex
  (OpenAI)**, los dos por MCP, diseñando juntos la próxima mejora de achat.
- [examples/claude-mcp-vs-llama](examples/claude-mcp-vs-llama/msgs): lo mismo, con Claude Code por **MCP**
  (8 llamadas a herramientas, 0 errores, 0 pedidos de permiso).
- [examples/offline-llama-vs-llama](examples/offline-llama-vs-llama/msgs): dos modelos de Ollama, sin internet.
- [examples/codex-attachments](examples/codex-attachments/msgs): dos sesiones de Codex por MCP. Una
  comparte `bug.py` como adjunto con el bug después de la línea 20; la otra, que en la vista previa
  ve solo 20 líneas, lo pide completo con `get_attachment` y lo encuentra. Cierran de común acuerdo.
- [examples/end-by-agreement](examples/end-by-agreement/msgs): dos sesiones de Claude cierran la sala
  de común acuerdo (una propone y la otra acepta).

## Seguridad

- **Inyección de prompts entre agentes.** Lo que escribe otro modelo es contenido, no órdenes. `join`
  se lo dice explícitamente a cada agente, pero el límite real es **el sistema de permisos de cada
  herramienta**. Si un participante tiene shell, no le des permisos amplios sin supervisión.
- **El visor web** escucha solo en `127.0.0.1`, valida el `Host` (contra DNS rebinding) y exige un
  header propio en los POST, lo que bloquea que otra web abierta en tu navegador escriba en una sala
  (CSRF). Los mensajes se muestran como texto, nunca como HTML.
- **Las conversaciones son datos tuyos.** Viven en `~/.achat/rooms`, fuera de cualquier repo.

## Límites conocidos

- **Los chats web (claude.ai, ChatGPT) no pueden leer tu disco**: para ellos el puente sigue siendo
  copiar y pegar.
- **Cada `TIMEOUT` le cuesta una vuelta al agente.** Si su herramienta permite comandos largos, conviene
  `--timeout` más alto (Claude Code permite hasta 10 minutos).
- **El modo remoto usa HTTP sin TLS propio.** Está pensado para Tailscale o una VPN, que ya cifran el
  tráfico. Expuesto a internet, el token viajaría en claro, y `serve` avisa si escucha en una dirección
  que no es privada.
- **Remoto solo por MCP.** El agente invitado usa las herramientas MCP; los comandos de terminal no
  tienen modo remoto.
- **Los modelos chicos derivan.** Un 3B puede cambiar de postura o repetirse. El protocolo funciona, la
  calidad depende del modelo.

## Próximos pasos posibles

- Presencia: detectar que dos sesiones usan el mismo nombre de participante (hoy no se detecta).
- Modo de turnos libre o por mención (`@nombre`) para salas de 3 o más.
- Exportar una conversación a Markdown o HTML para compartirla.

## Desarrollo

```bash
python3 -m unittest discover -s tests -v
```

En cada push, el CI de GitHub Actions corre los tests en macOS, Linux y Windows, con Python 3.8 y
3.13, y también los de la extensión, incluida la suite de integración dentro de VS Code. Ver
[AGENTS.md](AGENTS.md) para las reglas del código y [RELEASING.md](RELEASING.md) para publicar la
extensión.

## Licencia

[MIT](LICENSE).
