# Cómo publicar la extensión

El workflow [.github/workflows/release.yml](.github/workflows/release.yml) publica la extensión
cuando subís un tag `v*`. Cada tienda se publica solo si existe su token como *secret* del repo; si
falta, se saltea sin fallar.

Hay dos tiendas, y conviene publicar en las dos:

| Tienda | Desde dónde instalan | Secret |
|---|---|---|
| VS Code Marketplace | VS Code | `VSCE_PAT` |
| Open VSX | Cursor, Windsurf, Antigravity, VSCodium | `OVSX_PAT` |

## Una sola vez

Lo único que requiere tu login es crear las cuentas y los tokens (pasos 1 y 2 de la sección
siguiente, en el navegador). Todo lo demás lo hace un solo comando:

```bash
scripts/setup-store-publishing.sh            # pide el publisher ID y los dos tokens (sin mostrarlos)
scripts/setup-store-publishing.sh --dry-run  # muestra qué haría, sin cambiar nada
```

El script:
- verifica el token del Marketplace;
- crea el namespace en Open VSX;
- guarda `VSCE_PAT` y `OVSX_PAT` como secrets del repo (por stdin, nunca como argumentos);
- pone tu publisher y una versión nueva en `package.json`;
- commitea, taguea y hace push.

El workflow de release publica en las dos tiendas.

## Detalle: crear las cuentas (requiere tu login)

1. **VS Code Marketplace.**
   1. Entrá a <https://marketplace.visualstudio.com/manage> y creá un *publisher*. El ID es único y
      va en las URLs, por ejemplo `deevpfa`.
   2. En <https://dev.azure.com>, creá un *Personal Access Token* con "Organization: All accessible
      organizations" y el scope **Marketplace → Manage**.
   3. Guardalo en el repo con `gh secret set VSCE_PAT --repo deevpfa/achat`, que te lo pide sin
      mostrarlo en pantalla.
2. **Open VSX.**
   1. Entrá a <https://open-vsx.org> con GitHub y firmá el acuerdo de publicación (Eclipse).
   2. Creá un token en <https://open-vsx.org/user-settings/tokens>.
   3. Creá el namespace, con el mismo ID que el publisher:
      `npx ovsx create-namespace <publisher-id> -p <token>`.
   4. Guardalo en el repo con `gh secret set OVSX_PAT --repo deevpfa/achat`.
3. **El publisher en el código.** En `extensions/vscode/package.json`, cambiá `"publisher": "achat"`
   por tu ID de publisher. También cambia el ID de la extensión: `<publisher>.achat`.

## Cada versión

```bash
# 1. subí "version" en extensions/vscode/package.json (y VERSION en achat.py si cambió el CLI)
git commit -am "Release 0.3.1"
git tag v0.3.1
git push && git push --tags
```

El workflow corre los tests, verifica que el tag coincida con la versión, empaqueta, publica en las
tiendas que tengan token y crea un *release* de GitHub con el `.vsix` adjunto. Ese release sirve
aunque todavía no tengas cuentas en las tiendas: se instala con
`code --install-extension achat-X.Y.Z.vsix`.
