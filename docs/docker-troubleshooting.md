# Docker devices: troubleshooting

The **Docker** tab of *Add device* gives you a `docker run` command and a `docker-compose.yml` that start the
official `tailscale/tailscale` image on your tailnet. The same tips are in a yellow dropdown under the snippets;
the commands there already carry your container's name. Replace `tailscale-<host>` below with it.

Every entry here was reproduced on a real tailnet.

## How the snippets behave

- **First start:** the container signs in with the auth key and keeps its identity in its volume.
  `TS_AUTH_ONCE=true` makes a restart reuse that identity instead of spending the key again.
- **`TS_EXTRA_ARGS` is read once.** The image passes it to `tailscale up`, which only runs the first time. Editing
  it later changes nothing.
- **`TS_ROUTES` is read on every start.** The exit node (`0.0.0.0/0, ::/0`) and your subnet routes live there, so
  ticking *Offer to be an exit node* after the first deploy and recreating the container works.
- **Removing a route does not withdraw it.** Use `docker exec tailscale-<host> tailscale set --advertise-routes= --advertise-exit-node=false`.
- **`TS_DEBUG_FIREWALL_MODE=auto`** lets the container pick `nftables` or `iptables` to match the host.
- **The Compose auth key is in `.env`**, next to the compose file, so the file you share has no secret. After the first start delete that line: the container keeps its identity (Compose then warns that `TS_AUTHKEY` is not set, which is harmless).
- **The image is pinned** to a tested Tailscale version; *latest* is an explicit choice, because it can change these behaviours.
- **A running container** can be changed without recreating it with the `docker exec ... tailscale set` command under the snippets. A restart goes back to what `TS_ROUTES` says in the file, so change the file too.

## The container does not connect or stays offline

1. Read what it says: `docker logs tailscale-<host> --tail 50`.
2. The server's address has to be reachable *from inside the container*: `curl -I https://your-server`.
3. Use the scheme the server really answers on: `https://` with a certificate, `http://` only on a trusted network.
4. If the server's name only resolves on your local network, the tailnet's DNS cannot find it once the container
   uses it. Untick *Use this tailnet's DNS settings*, or use a public name or an IP.

## "authkey already used", "invalid key", or a restart loop

A key made in the Docker tab is single-use and expires in days. A container needs it only once, but a **new volume
needs a new key**. To start over, delete the old machine in *Machines*, generate a new key, and run the snippets on
a clean volume:

```bash
docker compose down -v
# or, with docker run:
docker rm -f tailscale-<host> && docker volume rm tailscale-<host>
```

## The container has no Internet

Test the host first. If this fails, the problem is the machine's network, not Tailscale:

```bash
docker run --rm alpine ping -c 3 1.1.1.1
docker run --rm alpine nslookup example.com
```

If only the name lookup fails, give Docker DNS servers in `/etc/docker/daemon.json`
(`{"dns": ["1.1.1.1"]}`) and restart Docker. Behind a proxy or a firewall, allow outgoing HTTPS and UDP.

## I cannot reach the Internet through the exit node

1. **Approve it.** Open the machine in *Machines* and enable the exit node, unless the policy approves it
   automatically. Until then no device can even choose it.
2. **Look for firewall errors:** `docker logs tailscale-<host> 2>&1 | grep -iE 'iptables|nftables|router'`.
   A line such as `can't initialize iptables table 'filter': Table does not exist` means the host kernel has no
   `iptables-legacy`. The container registers and shows as an exit node, but forwards nothing. This is the usual
   cause on recent Fedora, Debian and Ubuntu hosts that only have `nftables`. The snippets set
   `TS_DEBUG_FIREWALL_MODE=auto`; if it still fails, set it to `nftables` or `iptables`.
3. **Enable IP forwarding** on the host as well:

    ```bash
    echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
    echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
    sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
    ```

4. **Give it a few seconds.** The first connection after choosing an exit node can take that long. On Linux, keep
   your local network reachable with `tailscale set --exit-node=<host> --exit-node-allow-lan-access`.
5. A custom policy has to allow the devices that use the exit node to reach `autogroup:internet`.
6. As a last resort tick *Userspace networking*: it needs no kernel firewall, with lower performance.

## The exit node does not appear in the device's list

A device only lists an exit node once it is advertised **and** approved:

```bash
docker exec tailscale-<host> tailscale debug prefs | grep -A3 AdvertiseRoutes
```

It has to list `0.0.0.0/0` and `::/0`. If it shows `null`, the container was created without the exit node:
tick the option, use the new snippets and recreate the container, or run
`docker exec tailscale-<host> tailscale set --advertise-exit-node` to add it without losing its identity.

## I do not see the subnets I advertised

- Approve them in *Machines*, on the machine's page. Until then no device uses them.
- **Linux devices ignore advertised subnets** unless told to accept them: `sudo tailscale set --accept-routes`.
  The other apps have a *Use Tailscale subnets* switch.
- A subnet that overlaps the network the device is already on does not work: the local network wins.

## The container restarts saying it cannot enable IP forwarding

```
Failed to enable IP forwarding: ... read-only file system
```

It advertises routes or an exit node but was created without the sysctls. Add them, as the snippets do, or run the
container as privileged:

```yaml
sysctls:
  - net.ipv4.ip_forward=1
  - net.ipv6.conf.all.forwarding=1
```

## "operation not permitted", or no `/dev/net/tun`

The container needs the `NET_ADMIN` and `NET_RAW` capabilities and, on some hosts, the `/dev/net/tun` device.
Rootless Podman, LXC, some NAS systems and shared hosts do not allow them: tick *Userspace networking* and use the
new snippets, which need none of that.

## Names do not resolve, or resolve late

Right after a device starts using an exit node, name lookups can fail for a few seconds while the tunnel comes up.
Try again. See which servers the container uses with `docker exec tailscale-<host> tailscale dns status`; they come
from the server's DNS settings, which should list at least one global nameserver.

## The connection is slow or goes through a relay

`docker exec tailscale-<host> tailscale status` says `relay` when traffic goes through a relay server instead of
directly. Allow outgoing UDP on the host, and forward UDP port 41641 on the router if it is behind one.
