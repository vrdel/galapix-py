#!/home/daniel/.pyenv/versions/gvmm-py3/bin/python3

import os, sys, sqlite3, math, time, signal, argparse, shlex
import socket, select, subprocess, configparser, errno
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

DEFAULT_CFG = os.path.join(os.environ["HOME"], ".galapix", "galapix.cfg")
DEFAULT_BACKEND = "sdl"
DEFAULT_PYENV_ENV = "galapix-py"
DEFAULT_PY_PATTERNS = []
DEFAULT_PY_SORT = "mtime-reverse"
DEFAULT_PY_BACKGROUND = "4b5262"
DEFAULT_PY_SELECTION_BORDER = "B02A37"
DEFAULT_PY_SPACING = 3


def cleanup(server, sock):
    try:
        server.close()
    except OSError:
        pass

    try:
        os.unlink(sock)
    except OSError:
        if os.path.exists(sock):
            raise

def build_sdl_command(dirp, dbp, geom, title):
    cmd = [
        "galapix.sdl",
        "--threads", "6",
        "-g", geom,
        "-d", dbp,
        "--title", title,
        "view",
    ]
    if dirp:
        cmd.append(dirp)
    return cmd


def build_py_command(dirp, dbp, geom, title, pyenv_env, patterns):
    cmd = [
        "galapix-py",
        "--ignore-pattern-case",
    ]

    for pattern in patterns:
        cmd.extend(["-p", pattern])

    cmd.extend([
        "-d", dbp,
        "view",
        "--background-color", DEFAULT_PY_BACKGROUND,
        "--selection-border-color", DEFAULT_PY_SELECTION_BORDER,
        "--spacing", str(DEFAULT_PY_SPACING),
        "--show-filenames",
        "--sort", DEFAULT_PY_SORT,
        "--geometry", geom,
        "--title", title,
    ])
    if dirp:
        cmd.append(dirp)

    shell_lines = [
        'export PYENV_ROOT="$HOME/.pyenv"',
        'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"',
        'eval "$(pyenv init -)"',
        'pyenv activate %s' % shlex.quote(pyenv_env),
        "exec " + " ".join(shlex.quote(part) for part in cmd),
    ]
    return ["/bin/bash", "-lc", "\n".join(shell_lines)]


def start(dirp, dbp, geom, title, backend, pyenv_env, patterns, changes_track):
    rows = []

    if dirp and changes_track and os.path.exists(dbp + "/cache3.sqlite3"):

        conn = sqlite3.connect(dbp + "/cache3.sqlite3")
        # conn.text_factory = str
        cur = conn.cursor()

        for f in os.listdir(dirp):
            f = dirp + "/" + f
            f = "%%%s%%" % f
            tup = (f, )

            cur.execute('SELECT fileid, url, mtime FROM files WHERE url LIKE ?', tup)
            rows = cur.fetchall()


            for i in rows:
                dbid = i[0]
                dbpath = i[1].replace("file:", "")
                dbmtime = i[2]

                try:
                    filestat = os.stat(dbpath)
                    filemtime = math.trunc(filestat.st_mtime)
                except os.error:
                    cur.execute('DELETE FROM files WHERE fileid == ?', (dbid, ))
                    cur.execute('DELETE FROM tiles WHERE fileid == ?', (dbid, ))
                else:
                    if filemtime > dbmtime:
                        im = Image.open(dbpath)
                        t = (filemtime, filestat.st_size, im.size[0], im.size[1], dbid)
                        cur.execute('UPDATE files SET mtime=?, size=?, width=?, height=? WHERE fileid == ?', t)
                        cur.execute('DELETE FROM tiles WHERE fileid == ?', (dbid, ))

            conn.commit()

        conn.close()

    if backend == "py":
        cmd = build_py_command(dirp, dbp, geom, title, pyenv_env, patterns)
    else:
        cmd = build_sdl_command(dirp, dbp, geom, title)

    return subprocess.Popen(cmd, preexec_fn=os.setpgrp)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CFG)
    parser.add_argument("--backend", choices=("sdl", "py"), default=DEFAULT_BACKEND)
    parser.add_argument("--pyenv-env", default=DEFAULT_PYENV_ENV)
    args = parser.parse_args()

    Settings = {}
    GalaInstan = {}

    class proc(subprocess.Popen):
        pass

    config = configparser.ConfigParser()
    config.read(args.config)

    pixsock = config.defaults().get("pixsock")

    try:
        os.unlink(pixsock)
    except OSError:
        if os.path.exists(pixsock):
            raise

    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(pixsock)
        server.listen(1)
    except socket.error as m:
        print(m)
        sys.exit(1)

    poller = select.poll()
    poller.register(server.fileno(), select.POLLIN)

    class StaticVar:
        NumInstan = 0

    def terminate_all_instances():
        for inst in list(GalaInstan.values()):
            pid = inst.get("pid")
            if not pid:
                continue
            try:
                pgid = os.getpgid(pid)
            except OSError:
                continue
            try:
                os.killpg(pgid, signal.SIGTERM)
            except OSError:
                continue

    def shutdown(exit_code=1):
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        terminate_all_instances()
        cleanup(server, pixsock)
        sys.exit(exit_code)

    def sigcleanup(*_):
        shutdown(1)

    def chldcleanup(*msg):
        pid, status = os.wait()
        match = ""

        for inst in GalaInstan:
            if pid == GalaInstan[inst]["pid"]:
                match = inst
        if match:
            del GalaInstan[match]

        StaticVar.NumInstan -= 1
        if StaticVar.NumInstan == 0:
            cleanup(server, pixsock)
            sys.exit(1)


    for section in config.sections():
        if section.startswith("Dir"):
            Settings["dirp"] = config.get(section, "dirpath")
            Settings["dbp"] = config.get(section, "dbpath")
            Settings["title"] = config.get(section, "wintitle")
            Settings["geom"] = config.get(section, "geometry")
            Settings["patterns"] = DEFAULT_PY_PATTERNS[:]
            Settings["changes_track"] = config.getboolean(section, "changes_track", fallback=True)
            if config.has_option(section, "pattern"):
                Settings["patterns"].append(config.get(section, "pattern"))
            proc = start(
                Settings["dirp"],
                Settings["dbp"],
                Settings["geom"],
                Settings["title"],
                args.backend,
                args.pyenv_env,
                Settings["patterns"],
                Settings["changes_track"],
            )
            Settings["pid"] = proc.pid
            Settings["inst"] = proc
            GalaInstan[Settings["title"]] = Settings
            Settings = {}
            StaticVar.NumInstan = len(GalaInstan)

    signal.signal(signal.SIGCHLD, chldcleanup)
    signal.signal(signal.SIGINT, sigcleanup)
    signal.signal(signal.SIGTERM, sigcleanup)

    while True:
        try:
            event = poller.poll(None)
            if event[0][1] & select.POLLIN:
                conn, addr = server.accept()
                data = conn.recv(64)
                data = data.decode()
                data = data.split()
                if data[1] == "restart":
                    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                    for inst in GalaInstan:
                        if data[0] in inst:
                            pgid = os.getpgid(GalaInstan[inst]["pid"])
                            os.killpg(pgid, signal.SIGTERM)
                            time.sleep(1)
                            proc = start(
                                GalaInstan[inst]["dirp"],
                                GalaInstan[inst]["dbp"],
                                GalaInstan[inst]["geom"],
                                GalaInstan[inst]["title"],
                                args.backend,
                                args.pyenv_env,
                                GalaInstan[inst].get("patterns", DEFAULT_PY_PATTERNS),
                                GalaInstan[inst].get("changes_track", True),
                            )
                            GalaInstan[inst]["pid"] = proc.pid
                            GalaInstan[inst]["inst"] = proc
                        elif data[0] == "all":
                            pgid = os.getpgid(GalaInstan[inst]["pid"])
                            os.killpg(pgid, signal.SIGTERM)
                            time.sleep(1)
                            proc = start(
                                GalaInstan[inst]["dirp"],
                                GalaInstan[inst]["dbp"],
                                GalaInstan[inst]["geom"],
                                GalaInstan[inst]["title"],
                                args.backend,
                                args.pyenv_env,
                                GalaInstan[inst].get("patterns", DEFAULT_PY_PATTERNS),
                                GalaInstan[inst].get("changes_track", True),
                            )
                            GalaInstan[inst]["pid"] = proc.pid

                    signal.signal(signal.SIGCHLD, chldcleanup)


        except KeyboardInterrupt:
            shutdown(130)

        except select.error as m:
            if m.args and m.args[0] == errno.EINTR:
                poller.unregister(server.fileno())
                poller = select.poll()
                poller.register(server.fileno(), select.POLLIN)

main()
