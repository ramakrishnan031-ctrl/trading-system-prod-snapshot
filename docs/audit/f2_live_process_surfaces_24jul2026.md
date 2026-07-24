# F2 — live-process surfaces (RAW capture, 24-Jul-2026)

**Captured:** 2026-07-24 15:22 IST (Friday), read-only, from the RUNNING service before its 16:00 self-exit.
**Method:** `ss` / `/proc/<pid>/fd` / `systemctl show` over SSH. **No signals sent, no restart, no port connected.**
**Scope note (per §A3):** RAW capture only — NO interpretation, NO recommendation. This is one input to the F2 surface enumeration (C4), which has not been done. Do not read F2 conclusions into it here.

`MainPID = 1904864` · `ActiveState=active` · `SubState=running` · service `enabled`.

## Listening TCP sockets (`ss -tlnp`)
| Local Address:Port | backlog | owner (pid/fd) | reachability |
|---|---|---|---|
| `0.0.0.0:5000` | 1024 | python **1904864** fd=23 | all interfaces (externally reachable) |
| `127.0.0.1:8080` | 1024 | python **1904864** fd=27 | loopback only |
| `127.0.0.1:5001` | **1** | python **1904864** fd=11 | loopback only |
| `127.0.0.1:8500` | 1024 | python 1749999 fd=6 | loopback only (separate PID) |
| `100.74.84.44:443`, `:42423` | 4096 | (tailscaled) | tailnet iface |
| `0.0.0.0:22` (sshd), `0.0.0.0:111` (rpcbind), `127.0.0.53%lo:53`, `127.0.0.54:53` (resolved) | — | system | — |

Of the three sockets owned by the trading process (1904864): `0.0.0.0:5000` (fd 23), `127.0.0.1:8080` (fd 27), `127.0.0.1:5001` (fd 11).

## Port 5001 (the resume-script "instance lock")
```
LISTEN 0  1  127.0.0.1:5001  0.0.0.0:*  users:(("python",pid=1904864,fd=11))
```
A real TCP `LISTEN` socket, backlog **1**, loopback-only, owned by the main process (fd 11). Not a lock file. (A separate lock FILE also exists — `/tmp/trading-system.lock`, one fd — see below.)

## Unix sockets owned by 1904864
None. Every `u_str LISTEN` returned by `ss -xlnp` belongs to a system service (systemd user bus, gnupg agents, snapd, rpcbind, journal, udev, unified-monitoring-agent). The trading process holds no listening unix socket.

## `/proc/1904864/fd` summary (123 fds total)
```
 fd target types (counts):
   24  data_store/trading_system.db-wal
   24  data_store/trading_system.db
   24  data_store/analytics.db-wal
   24  data_store/analytics.db
   10  socket
    8  pipe
    1  anon_inode:[eventpoll]
    1  /tmp/trading-system.lock
    1  data_store/trading_system.db-shm
    1  data_store/analytics.db-shm
    1  /dev/null
    +  logs/{trades,system,reconciler,debug}_2026-07-24.log
 non-regular fds: 10 socket, 8 pipe (several shared inodes), 1 anon_inode:[eventpoll], 1 /dev/null.
 No FIFO, no inotify/anon_inode:[inotify], no /dev/* watch, no unix-socket listener.
```

## systemd unit — signal / restart config (`systemctl show`)
```
KillMode        = control-group
KillSignal      = 2            (SIGINT)
Restart         = on-failure
TimeoutStopUSec = 30s          (TimeoutStopSec)
WantedBy        = multi-user.target
ExecStop        = (unset)
ExecReload      = (unset)
RestartSec      = (unset -> default 100ms)
```
(End of raw capture.)
