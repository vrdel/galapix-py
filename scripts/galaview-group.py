#!/home/daniel/.pyenv/versions/gvmm-py3/bin/python3

import os, sys, time, signal, argparse
import socket, select, subprocess, configparser, errno

DEFAULT_CFG = os.path.join(os.environ["HOME"], ".galapix", "galagroup.cfg")
DEFAULT_BACKEND = "py"
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

def build_sdl_command(dbp, geom, title):
    return [
        "galapix.sdl",
        "--threads", "6",
        "-g", geom,
        "-d", dbp,
        "--title", title,
        "view",
    ]


def pyenv_environ(pyenv_env):
    pyenv_root = os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
    venv_dir = os.path.join(pyenv_root, "versions", pyenv_env)
    venv_bin = os.path.join(venv_dir, "bin")

    if not os.path.isdir(venv_bin):
        print(f"pyenv environment not found: {venv_bin}", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env["PYENV_ROOT"] = pyenv_root
    env["VIRTUAL_ENV"] = venv_dir
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    return env


def build_py_command(dbp, geom, title, patterns,
                     sort=DEFAULT_PY_SORT, background=DEFAULT_PY_BACKGROUND,
                     selection_border=DEFAULT_PY_SELECTION_BORDER,
                     spacing=DEFAULT_PY_SPACING):
    cmd = [
        "galapix-view",
        "--ignore-pattern-case",
    ]

    for pattern in patterns:
        cmd.extend(["-p", pattern])

    cmd.extend([
        "-d", dbp,
        "--background-color", background,
        "--selection-border-color", selection_border,
        "--spacing", str(spacing),
        "--show-filenames",
        "--sort", sort,
        "--geometry", geom,
        "--title", title,
    ])
    return cmd


def start(dbp, geom, title, backend, pyenv_env, patterns,
          sort=DEFAULT_PY_SORT, background=DEFAULT_PY_BACKGROUND,
          selection_border=DEFAULT_PY_SELECTION_BORDER, spacing=DEFAULT_PY_SPACING):
    if backend == "py":
        cmd = build_py_command(dbp, geom, title, patterns,
                               sort=sort, background=background,
                               selection_border=selection_border, spacing=spacing)
        env = pyenv_environ(pyenv_env)
    else:
        cmd = build_sdl_command(dbp, geom, title)
        env = None

    return subprocess.Popen(cmd, env=env, preexec_fn=os.setpgrp)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CFG)
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


    cfg_backend = config.defaults().get("backend", DEFAULT_BACKEND)
    cfg_pyenv_env = config.defaults().get("pyenv_env", DEFAULT_PYENV_ENV)
    cfg_sort = config.defaults().get("sort", DEFAULT_PY_SORT)
    cfg_background = config.defaults().get("background", DEFAULT_PY_BACKGROUND)
    cfg_selection_border = config.defaults().get("selection_border", DEFAULT_PY_SELECTION_BORDER)
    cfg_spacing = config.defaults().get("spacing", str(DEFAULT_PY_SPACING))

    for section in config.sections():
        if section.startswith("Dir"):
            Settings["dbp"] = config.get(section, "dbpath")
            Settings["title"] = config.get(section, "wintitle")
            Settings["geom"] = config.get(section, "geometry")
            Settings["patterns"] = DEFAULT_PY_PATTERNS[:]
            Settings["backend"] = config.get(section, "backend", fallback=cfg_backend)
            Settings["pyenv_env"] = config.get(section, "pyenv_env", fallback=cfg_pyenv_env)
            Settings["sort"] = config.get(section, "sort", fallback=cfg_sort)
            Settings["background"] = config.get(section, "background", fallback=cfg_background)
            Settings["selection_border"] = config.get(section, "selection_border", fallback=cfg_selection_border)
            Settings["spacing"] = int(config.get(section, "spacing", fallback=cfg_spacing))
            if config.has_option(section, "pattern"):
                Settings["patterns"].append(config.get(section, "pattern"))
            proc = start(
                Settings["dbp"],
                Settings["geom"],
                Settings["title"],
                Settings["backend"],
                Settings["pyenv_env"],
                Settings["patterns"],
                sort=Settings["sort"],
                background=Settings["background"],
                selection_border=Settings["selection_border"],
                spacing=Settings["spacing"],
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
                        if data[0] in inst or data[0] == "all":
                            pgid = os.getpgid(GalaInstan[inst]["pid"])
                            os.killpg(pgid, signal.SIGTERM)
                            time.sleep(1)
                            proc = start(
                                GalaInstan[inst]["dbp"],
                                GalaInstan[inst]["geom"],
                                GalaInstan[inst]["title"],
                                GalaInstan[inst].get("backend", cfg_backend),
                                GalaInstan[inst].get("pyenv_env", cfg_pyenv_env),
                                GalaInstan[inst].get("patterns", DEFAULT_PY_PATTERNS),
                                sort=GalaInstan[inst].get("sort", cfg_sort),
                                background=GalaInstan[inst].get("background", cfg_background),
                                selection_border=GalaInstan[inst].get("selection_border", cfg_selection_border),
                                spacing=int(GalaInstan[inst].get("spacing", cfg_spacing)),
                            )
                            GalaInstan[inst]["pid"] = proc.pid
                            GalaInstan[inst]["inst"] = proc

                    signal.signal(signal.SIGCHLD, chldcleanup)


        except KeyboardInterrupt:
            shutdown(130)

        except select.error as m:
            if m.args and m.args[0] == errno.EINTR:
                poller.unregister(server.fileno())
                poller = select.poll()
                poller.register(server.fileno(), select.POLLIN)

main()
