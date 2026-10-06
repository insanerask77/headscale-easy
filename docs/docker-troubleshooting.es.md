# Dispositivos Docker: solución de problemas

La pestaña **Docker** de *Añadir dispositivo* te da un comando `docker run` y un `docker-compose.yml` que arrancan
la imagen oficial `tailscale/tailscale` en tu tailnet. Los mismos consejos están en un desplegable amarillo bajo los
fragmentos; allí los comandos ya llevan el nombre de tu contenedor. Sustituye `tailscale-<host>` por el tuyo.

Cada entrada se reprodujo en un tailnet real.

## Cómo se comportan los fragmentos

- **Primer arranque:** el contenedor inicia sesión con la clave y guarda su identidad en su volumen.
  `TS_AUTH_ONCE=true` hace que un reinicio reutilice esa identidad en vez de gastar la clave otra vez.
- **`TS_EXTRA_ARGS` se lee una sola vez.** La imagen se lo pasa a `tailscale up`, que solo se ejecuta la primera vez.
  Editarlo después no cambia nada.
- **`TS_ROUTES` se lee en cada arranque.** El nodo de salida (`0.0.0.0/0, ::/0`) y tus rutas de subred viven ahí, así
  que marcar *Ofrecerse como nodo de salida* después del primer despliegue y recrear el contenedor funciona.
- **Quitar una ruta no la retira.** Usa `docker exec tailscale-<host> tailscale set --advertise-routes= --advertise-exit-node=false`.
- **`TS_DEBUG_FIREWALL_MODE=auto`** deja que el contenedor elija `nftables` o `iptables` según el host.
- **La clave de Compose va en `.env`**, junto al archivo compose, así el archivo que compartes no lleva ningún secreto. Tras el primer arranque borra esa línea: el contenedor conserva su identidad (Compose avisará de que `TS_AUTHKEY` no está definida, es inofensivo).
- **La imagen está fijada** a una versión probada de Tailscale; *latest* es una elección explícita, porque puede cambiar estos comportamientos.
- **Un contenedor en marcha** se cambia sin recrearlo con el comando `docker exec ... tailscale set` bajo los fragmentos. Un reinicio vuelve a lo que diga `TS_ROUTES` en el archivo, así que cambia también el archivo.

## El contenedor no se conecta o sigue sin conexión

1. Lee lo que dice: `docker logs tailscale-<host> --tail 50`.
2. La dirección del servidor debe ser alcanzable *desde dentro del contenedor*: `curl -I https://tu-servidor`.
3. Usa el esquema en el que responde el servidor: `https://` con certificado, `http://` solo en una red de confianza.
4. Si el nombre del servidor solo se resuelve en tu red local, el DNS del tailnet no lo encuentra cuando el
   contenedor lo usa. Desmarca *Usar los ajustes de DNS de este tailnet*, o usa un nombre público o una IP.

## «authkey already used», «invalid key» o un bucle de reinicios

Una clave creada en la pestaña Docker es de un solo uso y caduca en días. Un contenedor la necesita solo una vez,
pero un **volumen nuevo necesita una clave nueva**. Para empezar de cero, elimina el dispositivo antiguo en
*Dispositivos*, genera una clave nueva y ejecuta los fragmentos con un volumen limpio:

```bash
docker compose down -v
# o, con docker run:
docker rm -f tailscale-<host> && docker volume rm tailscale-<host>
```

## El contenedor no tiene Internet

Prueba primero el host. Si esto falla, el problema es la red de la máquina, no Tailscale:

```bash
docker run --rm alpine ping -c 3 1.1.1.1
docker run --rm alpine nslookup example.com
```

Si solo falla la resolución de nombres, da servidores DNS a Docker en `/etc/docker/daemon.json`
(`{"dns": ["1.1.1.1"]}`) y reinicia Docker. Tras un proxy o un cortafuegos, permite HTTPS y UDP de salida.

## No consigo salir a Internet por el nodo de salida

1. **Apruébalo.** Abre el dispositivo en *Dispositivos* y activa el nodo de salida, salvo que la política lo apruebe
   automáticamente. Hasta entonces ningún dispositivo puede ni elegirlo.
2. **Busca errores de cortafuegos:** `docker logs tailscale-<host> 2>&1 | grep -iE 'iptables|nftables|router'`.
   Una línea como `can't initialize iptables table 'filter': Table does not exist` significa que el kernel del host
   no tiene `iptables-legacy`. El contenedor se registra y aparece como nodo de salida, pero no reenvía nada. Es la
   causa habitual en hosts recientes de Fedora, Debian y Ubuntu que solo tienen `nftables`. Los fragmentos fijan
   `TS_DEBUG_FIREWALL_MODE=auto`; si aun así falla, ponlo en `nftables` o en `iptables`.
3. **Activa el reenvío de IP** también en el host:

    ```bash
    echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
    echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
    sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
    ```

4. **Dale unos segundos.** La primera conexión tras elegir un nodo de salida puede tardar eso. En Linux, mantén
   accesible tu red local con `tailscale set --exit-node=<host> --exit-node-allow-lan-access`.
5. Una política personalizada debe permitir que los dispositivos que usan el nodo de salida lleguen a `autogroup:internet`.
6. Como último recurso marca *Red en espacio de usuario*: no necesita cortafuegos del kernel, con menos rendimiento.

## El nodo de salida no aparece en la lista del dispositivo

Un dispositivo solo lista un nodo de salida cuando está anunciado **y** aprobado:

```bash
docker exec tailscale-<host> tailscale debug prefs | grep -A3 AdvertiseRoutes
```

Debe listar `0.0.0.0/0` y `::/0`. Si muestra `null`, el contenedor se creó sin el nodo de salida: marca la opción,
usa los nuevos fragmentos y recrea el contenedor, o ejecuta
`docker exec tailscale-<host> tailscale set --advertise-exit-node` para añadirlo sin perder su identidad.

## No veo las subredes que he anunciado

- Apruébalas en *Dispositivos*, en la página del dispositivo. Hasta entonces ningún dispositivo las usa.
- **Los dispositivos Linux ignoran las subredes anunciadas** si no se les dice que las acepten:
  `sudo tailscale set --accept-routes`. Las otras apps tienen un interruptor *Usar subredes de Tailscale*.
- Una subred que se solapa con la red en la que ya está el dispositivo no funciona: gana la red local.

## El contenedor se reinicia diciendo que no puede activar el reenvío de IP

```
Failed to enable IP forwarding: ... read-only file system
```

Anuncia rutas o un nodo de salida pero se creó sin los sysctls. Añádelos, como hacen los fragmentos, o ejecuta el
contenedor como privilegiado:

```yaml
sysctls:
  - net.ipv4.ip_forward=1
  - net.ipv6.conf.all.forwarding=1
```

## «operation not permitted», o falta `/dev/net/tun`

El contenedor necesita las capacidades `NET_ADMIN` y `NET_RAW` y, en algunos hosts, el dispositivo `/dev/net/tun`.
Podman sin root, LXC, algunos NAS y hosts compartidos no los permiten: marca *Red en espacio de usuario* y usa los
nuevos fragmentos, que no necesitan nada de eso.

## Los nombres no se resuelven, o tardan

Justo después de que un dispositivo empiece a usar un nodo de salida, la resolución de nombres puede fallar unos
segundos mientras sube el túnel. Vuelve a probar. Mira qué servidores usa el contenedor con
`docker exec tailscale-<host> tailscale dns status`; vienen de los ajustes de DNS del servidor, que debería listar
al menos un servidor de nombres global.

## La conexión es lenta o pasa por un relé

`docker exec tailscale-<host> tailscale status` dice `relay` cuando el tráfico pasa por un servidor de relé en vez de
ir directo. Permite UDP de salida en el host y reenvía el puerto UDP 41641 en el router si hay uno por medio.
