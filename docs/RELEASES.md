# Releases del edge_agent (actualización automática)

Las Raspberry en campo se actualizan solas: cada 3 horas buscan en GitHub un
tag `vX.Y.Z` nuevo, **en `main` y firmado** por una clave autorizada, lo
instalan y, si el servicio no queda sano, vuelven a la versión anterior
(detalle en `INSTALACION_RASPBERRY.md` §11).

Crear un tag es, en la práctica, desplegar a todos los equipos. Este documento
es el procedimiento para hacerlo.

## 1. Una sola vez: clave de firma

Cada persona que publica releases necesita una clave SSH propia de firma.

```bash
ssh-keygen -t ed25519 -C "releases SGPMP" -f ~/.ssh/sgpmp_release
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/sgpmp_release.pub
```

Proteger la clave privada con contraseña y no copiarla a ningún equipo de
campo: quien la tenga puede instalar código en todas las Raspberry.

Registrar la clave **pública** en `raspberry/release/allowed_signers`, una
línea por clave, por un PR normal:

```
SerBy48 namespaces="git" ssh-ed25519 AAAA... releases SGPMP
```

Una Raspberry confía en las claves que tenía el `allowed_signers` de la
versión que se le instaló. Por eso:

- **La primera clave tiene que estar en el repo antes de instalar las Pi a
  mano.** Una Pi instalada con el archivo vacío no acepta ningún release y
  hay que reinstalarla a mano.
- Agregar o quitar claves después es un cambio como cualquier otro: llega en
  un release firmado con una clave que la Pi ya acepta.

Opcional, para ver los tags como "Verified" en GitHub: agregar la misma clave
pública en GitHub → Settings → SSH and GPG keys → New SSH key → tipo
*Signing key*.

## 2. Una sola vez: proteger los tags en GitHub

GitHub → repo → Settings → Rules → Rulesets → *New tag ruleset*:

- Target: tags que coincidan con `v*`.
- Restringir creación, actualización y borrado.
- Bypass: solo los mantenedores que publican releases.

La firma ya impide que un tag ajeno se instale; esto evita además que se
borren o muevan los tags publicados.

## 3. Publicar un release

1. En `develop`, subir la versión en `raspberry/pyproject.toml` y
   `raspberry/edge_agent/__init__.py` (ej. `0.2.0`), por PR.
2. PR `develop → main` (Git Flow) con el CI en verde.
3. Probar `main` en una Pi de laboratorio instalada con `--no-auto-update`
   (instalación manual, `INSTALACION_RASPBERRY.md` §11).
4. Crear el tag firmado sobre `main` y publicarlo:

   ```bash
   git checkout main && git pull --ff-only
   git tag -s v0.2.0 -m "v0.2.0: <resumen>"
   git -c gpg.ssh.allowedSignersFile=raspberry/release/allowed_signers tag -v v0.2.0
   git push origin v0.2.0
   ```

   `tag -v` debe decir `Good "git" signature`. Si no, la Pi tampoco lo
   aceptará.
5. Seguimiento: en ≤ 3,5 h cada equipo publica `version_firmware=v0.2.0` en el
   heartbeat. Para no esperar en un equipo con acceso:
   `sudo systemctl start edge-updater` y `journalctl -u edge-updater`.

## 4. Reglas

- **Compatibilidad del estado local.** Un release tiene que leer lo que dejó
  la versión anterior en `/var/lib/sgpmp-edge/` (`buffer.db`, `config.json`,
  `umbrales.json`), y la anterior tiene que seguir leyéndolo después: el
  rollback reinstala código, no deshace cambios de datos.
- **Nunca mover ni borrar un tag publicado.** Para retirar un release malo,
  publicar uno nuevo más alto (ej. `v0.2.1` con el revert). Las Pi nunca bajan
  de versión solas.
- **El rollback automático solo cubre** que el servicio no arranque o se
  reinicie en los primeros 3 minutos. Un error de lógica que no tumba el
  proceso (datos mal enviados, comandos mal aplicados) no lo detecta: eso lo
  cubren el CI y la prueba en la Pi de laboratorio.
- **Cambios en `/etc/sgpmp/edge-agent.env`** no llegan por release: si una
  versión necesita una variable nueva obligatoria, esa versión debe traer un
  default o la Pi quedará fallando `--check-config` y volverá a la anterior.
