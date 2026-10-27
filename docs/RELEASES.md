# Releases del edge_agent (actualización automática)

Las Raspberry en campo se actualizan solas: cada 3 horas buscan en GitHub un
tag `vX.Y.Z` nuevo **que esté en `main`**, lo instalan y, si el servicio no
queda sano, vuelven a la versión anterior (detalle en
`INSTALACION_RASPBERRY.md` §11).

Los tags los crea el CI (**versionamiento automático**, mismo esquema que
`BROKER-MQTT-SGPMP`): la versión sale de los mensajes de commit, `develop`
publica release candidates (`v0.3.0-rc.1`, las Pi los ignoran) y un merge a
`main` con cambios publica la versión final (`v0.3.0`). En la práctica,
**fusionar en `main` un `feat` o un `fix` es desplegar a todos los equipos.**
Este documento es el procedimiento para hacerlo y para mantenerlo seguro.

## 1. Una sola vez: quién puede desplegar

La Raspberry instala cualquier tag `v*` que esté en `main`. La protección está
en GitHub (repo → Settings):

- **Rules → Rulesets → New branch ruleset** sobre `main`: PR obligatorio con
  aprobación, sin push directo ni force push (convención Git Flow del repo).
- **Rules → Rulesets → New tag ruleset** sobre `v*`: restringir creación,
  actualización y borrado; *bypass* solo para la cuenta del `GH_TOKEN` (el
  CI crea los tags).
- **Collaborators**: revisar periódicamente quién tiene acceso y con qué rol.
- **Settings → Secrets and variables → Actions → New repository secret**
  `GH_TOKEN`: un token personal (*fine-grained*, solo este repo, permiso
  *Contents: Read and write*) de una cuenta que esté en el *bypass* de los
  rulesets de `main`, `develop` y `v*`. El CI lo usa para empujar el commit de
  release, el tag y el merge de `main` a `develop`; el token automático de
  Actions no puede, porque no está en el bypass. Al vencer o al traspasar el
  proyecto, reemplazarlo por uno del nuevo responsable (es el mismo esquema
  que el broker).

Con esto, desplegar requiere un PR revisado hacia `main` con el CI en verde.
Si más adelante hace falta más garantía (muchos colaboradores, equipos
críticos), ver la firma de tags (§5).

## 2. Publicar un release

La versión la decide el tipo de commit (Conventional Commits), desde el último
tag final (el primero, `v0.1.0`, se creó a mano):

| Commit | Release |
|---|---|
| `feat(...)` | menor: `0.2.0 → 0.3.0` |
| `fix`, `perf`, `refactor` | parche: `0.2.0 → 0.2.1` |
| `feat!:` o `BREAKING CHANGE:` en el cuerpo | mayor: `0.2.0 → 1.0.0` |
| `docs`, `chore`, `test`, `ci` | ninguno |

Los PR se fusionan con *merge commit* (como hasta ahora), así cuentan los
commits de la rama. Con *squash*, lo que cuenta es el título del PR, que
entonces tiene que seguir el mismo formato.

1. Cada PR a `develop` publica un `vX.Y.Z-rc.N` (si trae `feat`/`fix`/...).
   Las Pi no los instalan: sirven para saber qué versión saldrá.
2. Probar `develop` en una Pi de laboratorio instalada con `--no-auto-update`
   (instalación manual, `INSTALACION_RASPBERRY.md` §11).
3. PR `develop → main`. Al fusionarlo, con el CI en verde, el job
   *Versionamiento*:
   - crea el tag `vX.Y.Z` y el GitHub Release con las notas;
   - hace el commit `chore(release): X.Y.Z [skip ci]` en `main` con
     `CHANGELOG.md` y la versión en `raspberry/pyproject.toml` y
     `raspberry/edge_agent/__init__.py`;
   - fusiona `main` de vuelta en `develop`, para que los siguientes rc partan
     de la versión nueva.
4. Seguimiento: en ≤ 3,5 h cada equipo publica `version_firmware=vX.Y.Z` en
   el heartbeat. Para no esperar en un equipo con acceso:
   `sudo systemctl start edge-updater` y `journalctl -u edge-updater`.

Para fusionar en `main` sin desplegar, el PR solo puede traer commits que no
generan release (`docs`, `chore`, `ci`, `test`).

Si el job falla, el tag no se crea y las Pi no cambian: revisar el log del job
en Actions, corregir y volver a correrlo (*Re-run jobs*).

## 3. Reglas

- **Compatibilidad del estado local.** Un release tiene que leer lo que dejó
  la versión anterior en `/var/lib/sgpmp-edge/` (`buffer.db`, `config.json`,
  `umbrales.json`), y la anterior tiene que seguir leyéndolo después: el
  rollback reinstala código, no deshace cambios de datos.
- **Nunca mover ni borrar un tag publicado.** Para retirar un release malo,
  publicar uno nuevo más alto (un `fix` o un `revert` por PR: sale `v0.2.1`).
  Las Pi nunca bajan de versión solas.
- **No editar a mano** `CHANGELOG.md` ni la versión en `pyproject.toml` /
  `__init__.py`: los escribe el CI en `main`. Si `develop` también los toca,
  el merge automático de `main` a `develop` choca; en ese caso el job falla y
  hay que hacerlo por PR (rama desde `develop`, `git merge origin/main`,
  resolver, PR con *merge commit*).
- **El rollback automático solo cubre** que el servicio no arranque o se
  reinicie en los primeros 3 minutos. Un error de lógica que no tumba el
  proceso (datos mal enviados, comandos mal aplicados) no lo detecta: eso lo
  cubren el CI y la prueba en la Pi de laboratorio.
- **Cambios en `/etc/sgpmp/edge-agent.env`** no llegan por release: si una
  versión necesita una variable nueva obligatoria, esa versión debe traer un
  default o la Pi quedará fallando `--check-config` y volverá a la anterior.

## 4. Pasar el repo a privado

Mientras el repo es público, la Pi descarga por HTTPS sin credenciales. Al
volverlo privado, GitHub rechaza esa descarga y **las Pi sin credencial dejan
de actualizarse** (en `journalctl -u edge-updater` aparece
`Repository not found` o `Authentication failed`). La credencial no puede
llegar por la propia actualización: hay que instalarla en cada Pi **antes**
del cambio, o entrar a cada una después.

Se usa una **clave de deploy** de GitHub: una clave SSH de solo lectura que
pertenece al repositorio, no a una persona (sigue funcionando aunque cambie
quién mantiene el proyecto). No usar un token personal: depende de una
cuenta y vence.

1. Crear la clave (en cualquier PC, sin contraseña porque la usa un servicio):

   ```bash
   ssh-keygen -t ed25519 -N '' -C "sgpmp-edge deploy" -f deploy_key
   ```

2. GitHub → repo → Settings → Deploy keys → *Add deploy key*: pegar
   `deploy_key.pub`, **sin** marcar *Allow write access*.
3. En cada Pi, copiar `deploy_key` (la privada) e instalarla:

   ```bash
   sudo ./raspberry/scripts/install.sh --deploy-key ~/deploy_key
   shred -u ~/deploy_key
   ```

   Queda en `/etc/sgpmp/deploy_key` (0600, root) y `EDGE_UPDATE_REPO` pasa a
   `git@github.com:SerBy48/EDGE-FIRMWARE-SGPMP.git` en
   `/etc/sgpmp/edge-updater.env`. Funciona igual con el repo todavía público.
4. Comprobar: `sudo /opt/sgpmp-edge/bin/update.sh --check` sin errores.
5. Recién entonces cambiar el repo a privado.

Una clave por Pi permite revocar un equipo perdido sin tocar los demás; una
compartida es más simple de instalar. Si se filtra, solo da lectura del
código: borrarla en GitHub y repetir el paso 3 con una nueva.

Las Pi nuevas, ya con el repo privado, se instalan con la clave desde el
principio (`INSTALACION_RASPBERRY.md` §4 y §6).

## 5. Opcional: exigir tags firmados

**No es compatible con el versionamiento automático**: los tags los crea el
CI, que no tiene la clave de firma de nadie. Activarlo implica volver a crear
los tags a mano (o darle al CI una clave de firma propia en un secret, que
pasa a ser tan sensible como el `GH_TOKEN`).

Con `EDGE_UPDATE_REQUIRE_SIGNATURE=1` en `/etc/sgpmp/edge-updater.env`, la Pi
solo instala tags firmados por una clave de
`raspberry/release/allowed_signers`. Protege incluso si una cuenta de
administrador del repo queda comprometida. El costo: quien publica releases
debe tener una clave de firma, y al traspasar el proyecto hay que agregar la
del nuevo responsable.

1. Cada persona que publica crea su clave de firma (con contraseña) y
   configura git:

   ```bash
   ssh-keygen -t ed25519 -C "releases SGPMP" -f ~/.ssh/sgpmp_release
   git config --global gpg.format ssh
   git config --global user.signingkey ~/.ssh/sgpmp_release.pub
   ```

2. Agregar su clave **pública** a `raspberry/release/allowed_signers` por PR:

   ```
   <usuario> namespaces="git" ssh-ed25519 AAAA... releases SGPMP
   ```

3. Publicar un release normal (§2) que lleve ese `allowed_signers`, para que
   llegue a las Pi.
4. Activar en cada Pi: `EDGE_UPDATE_REQUIRE_SIGNATURE=1` en
   `/etc/sgpmp/edge-updater.env`. Requiere entrar a cada equipo (igual que
   la clave de deploy): conviene decidirlo antes de instalar en campo.
5. Desde ahí, los tags se crean firmados y se verifican antes de publicar:

   ```bash
   git tag -s v0.3.0 -m "v0.3.0: <resumen>"
   git -c gpg.ssh.allowedSignersFile=raspberry/release/allowed_signers tag -v v0.3.0
   git push origin v0.3.0
   ```

Para traspasar el proyecto: el nuevo responsable agrega su clave pública, se
publica un release firmado por una clave ya aceptada y, en el siguiente, se
quita la anterior.
