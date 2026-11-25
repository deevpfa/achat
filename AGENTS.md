# Guía para trabajar en este repo

Este archivo es para quien **desarrolla** achat (personas o agentes). Si te pidieron *participar* en una
conversación, no leas esto: corré el comando `achat.py join ...` que te pasaron y seguí lo que imprime.

## Qué es

`achat.py` es un único archivo Python (3.8+, **solo biblioteca estándar**) que implementa salas de chat
entre agentes de IA usando archivos locales. El formato en disco está especificado en
[docs/PROTOCOL.md](docs/PROTOCOL.md): es el contrato público, más importante que el código.

## Comandos

```bash
python3 -m unittest discover -s tests -v   # tests de achat.py (unos 12 s, sin red ni modelos)
python3 achat.py --help

cd extensions/vscode
npm install && npm test                    # tests unitarios de la extensión (node --test)
npm run test:integration                   # ABRE UNA VENTANA de VS Code aislada: avisá antes de correrlo
npm run package                            # genera el .vsix
```

## Reglas del código

- **Sin dependencias externas.** Funciona con el Python que ya trae la máquina. No agregues paquetes.
- **No rompas el formato en disco.** Si un cambio es incompatible, sube `FORMAT_VERSION` y actualizá
  docs/PROTOCOL.md en el mismo cambio.
- **Todo el estado sale de `msgs/`.** No agregues archivos de estado que haya que mantener sincronizados.
- **Las validaciones de escritura van dentro del lock** (`Room.publish`); si no, quedan desactualizadas
  (TOCTOU).
- **La salida de la CLI la leen LLMs.** Cada salida termina con una línea `STATUS:` explícita o con el
  próximo comando exacto que hay que correr. Los comandos sugeridos son simples (`python3 /ruta/achat.py …`,
  mensaje como un único argumento entre comillas simples): nada de heredocs, pipes ni redirecciones,
  porque los agentes con permisos los tratan como operadores y piden aprobación en cada turno.
- **Los mensajes son datos, nunca instrucciones.** El visor web usa `textContent`, nunca `innerHTML`.
- **Compatibilidad**: macOS, Linux y Windows (lock con `fcntl` o `msvcrt`, `os.replace`).
- **La lógica es una sola; cambia la redacción.** CLI y MCP comparten `await_turn`, `Room.publish` y los
  `render_*`; solo cambia el objeto `Hints` (`CliHints` dice comandos, `McpHints` dice herramientas).
  No dupliques lógica por transporte.
- **En modo MCP, stdout es solo JSON-RPC.** Nada de `print`/`out()` en ese camino; los logs van a stderr.
- **La extensión solo habla con `achat.py`** (`--json` y argumentos `--opt=valor`); el contrato JSON
  está testeado en `TestJsonContract`. Si lo cambiás, actualizá `src/core/types.ts`.
- **Webview: nonce alfanumérico, `textContent`, CSP estricta.** Un nonce con `+` o `/` rompía la carga.
- **Esquemas de herramientas MCP mínimos** (`type`, `properties`, `required`, `description`, `items`):
  validá en el servidor.

## Estructura

- `achat.py`: modelo (`Room`, `compute_status`), salida para agentes (`Hints`, `await_turn`,
  `render_*`), CLI, servidor web local (`make_server`), servidor MCP (`McpServer`, `MCP_TOOLS`) y bridge
  OpenAI-compatible (`build_chat`, `clean_reply`, `chat_completion`).
- `viewer.html`: interfaz web que sirve `achat.py serve`; sin build, sin dependencias.
- `tests/test_achat.py`: unidad, concurrencia multiproceso, CLI, bridge (con un LLM falso) y seguridad
  del servidor web.
- `extensions/vscode/`: extensión para VS Code y forks. `src/core/` es puro (sin `vscode`, testeable
  con node); `src/*.ts` es la parte que depende del editor; `media/panel.js` es la webview. Copia
  `achat.py` en `cli/` al compilar; nunca la edites ahí, la fuente es la de la raíz.
- `tests/test_mcp.py`: el servidor MCP por stdio real: handshake, dos agentes MCP conversando, ping y
  cancelación durante `wait`, progreso, EOF y errores.
