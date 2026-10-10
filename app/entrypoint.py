"""Container start: give /config to the right user, drop root, then run the web app.

Unraid keeps appdata owned by nobody:users (99:100). The container starts as root only long enough to hand over
the folder, then continues as PUID:PGID so the files it writes have the right owner.
"""
import os
import sys

DEFAULT_PUID, DEFAULT_PGID = 99, 100


def _number(value: str | None, default: int) -> int:
    try:
        n = int(value or "")
    except ValueError:
        return default
    return n if n >= 0 else default


class RealOps:
    def makedirs(self, path):
        os.makedirs(path, exist_ok=True)

    def chown_tree(self, path, uid, gid):
        os.chown(path, uid, gid)
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                os.chown(os.path.join(root, name), uid, gid)

    def drop(self, uid, gid):
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)


def prepare(env, current_uid: int, ops) -> tuple[int, int] | None:
    """Hand /config to PUID:PGID and drop to them. Returns the ids used, or None when nothing was changed."""
    if current_uid != 0:
        return None                      # already started as a normal user (docker run --user)
    puid = _number(env.get("PUID"), DEFAULT_PUID)
    pgid = _number(env.get("PGID"), DEFAULT_PGID)
    config = env.get("CONFIG_DIR", "/config")
    ops.makedirs(config)
    if puid == 0:
        return None                      # PUID=0 means: stay root
    try:
        ops.chown_tree(config, puid, pgid)
    except OSError as e:                 # for example a bind mount that does not allow chown
        print(f"Could not change the owner of {config}: {e}", flush=True)
    ops.drop(puid, pgid)
    return puid, pgid


def main() -> None:
    ids = prepare(os.environ, os.getuid(), RealOps())
    if ids:
        os.environ["HOME"] = "/tmp"
        print(f"Running as {ids[0]}:{ids[1]}", flush=True)
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"])


if __name__ == "__main__":
    main()
