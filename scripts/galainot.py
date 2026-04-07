#!/home/daniel/.pyenv/versions/gvmm-py3/bin/python3

import os, sys, signal, socket, select, errno, multiprocessing
import subprocess, time, re, threading, _thread, configparser, fcntl

try:
	import pyinotify
except Exception:
	pyinotify = None

CFG = os.environ['HOME'] + "/.galapix/galapix.cfg"

def callcom(commexe, po, event=None):
	if not event:
		event = "gvmm.py"

	proc = subprocess.run(commexe, shell=True, capture_output=True, text=True)
	output = proc.stdout or ""
	errout = proc.stderr or ""
	if proc.returncode != 0:
		msg = errout.strip() if errout.strip() else output.strip()
		print("- " + time.strftime("%H:%M:%S") + " [%s]" % (event) \
			+ ": command failed (exit %d)%s" % (
				proc.returncode,
				(" - " + msg.replace("\n", " ")) if msg else ""
			))
		return

	if po:
		output = output.replace("\n\n", "<EMPTYL>")
		output = output.split("<EMPTYL>")[0]
		print("- " + time.strftime("%H:%M:%S") + " [%s]" % (event) \
			+ ": " + output.replace("\n", " "))

def _snapshot_tree(watchroot):
	snapshot = {}
	for (dirpath, dirnames, filenames) in os.walk(watchroot, followlinks=True):
		if filenames:
			for f in filenames:
				p = os.path.realpath(os.path.join(dirpath, f))
				try:
					st = os.stat(p)
				except OSError:
					continue
				snapshot[p] = (st.st_mtime_ns, st.st_size)
	return snapshot


def inotifymon_poll(commexe, watchroot, interval=0.8):
	prev = _snapshot_tree(watchroot)
	while True:
		time.sleep(interval)
		cur = _snapshot_tree(watchroot)
		if cur != prev:
			callcom(commexe, True, "poll-change")
			prev = cur


def inotifymon(commexe, watchroot):
	if pyinotify is None:
		inotifymon_poll(commexe, watchroot)
		return

	wdd = {}

	class EventHandler(pyinotify.ProcessEvent):
		def _restartinst(self, event, typ):
			if typ == "delete":
				if os.path.exists(event.pathname):
					del (wdd[event.pathname])
			elif typ == "create":
				wdd.update(wm.add_watch(event.pathname,mask))
			elif typ == "deleteself":
				del (wdd[event.pathname])
				if os.path.exists(event.pathname):
					wdd.update(wm.add_watch(event.pathname, mask))
			callcom(commexe, True, event.maskname)

		def process_IN_DELETE(self, event):
			self._restartinst(event, "delete")
		def process_IN_DELETE_SELF(self, event):
			self._restartinst(event, "deleteself")
		def process_IN_CREATE(self, event):
			self._restartinst(event, "create")
		def process_IN_MODIFY(self, event):
			self._restartinst(event, "modify")
		def process_IN_CLOSE_WRITE(self, event):
			self._restartinst(event, "in_close_write")

	wm = pyinotify.WatchManager()
	mask = pyinotify.IN_DELETE_SELF | pyinotify.IN_DELETE | pyinotify.IN_CREATE | pyinotify.IN_CLOSE_WRITE

	handler = EventHandler()

	wdd.update(wm.add_watch(watchroot, mask, rec=True))
	for (dirpath, dirnames, filenames) in os.walk(watchroot, followlinks=True):
		if filenames:
			for f in filenames:
				wdd.update(wm.add_watch(os.path.realpath(dirpath + '/' + f), mask))

	notifier = pyinotify.Notifier(wm, handler)
	notifier.loop()

def main():
	config = configparser.ConfigParser()
	config.read(CFG)

	inotsock = config.defaults().get("inotsock")

	try:
		os.unlink(inotsock)
	except OSError:
		if os.path.exists(inotsock):
			raise

	try:
		server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		server.bind(inotsock)
		server.listen(1)
	except socket.error as m:
		print(m)
		sys.exit(1)


	commexe = ""
	commexe = " ".join(sys.argv[2:])
	watchroot = sys.argv[1]

	callcom(commexe, False)

	proc = multiprocessing.Process(target=inotifymon, args=(commexe, watchroot))
	proc.start()

	poller = select.poll()
	poller.register(server.fileno(), select.POLLIN)

	while True:
		try:
			event = poller.poll(None)
			if event[0][1] & select.POLLIN:
				conn, addr = server.accept()
				data = conn.recv(64)
				callcom(commexe, True)

		except select.error as m:
			print(m)
			if m.args and m.args[0] == errno.EINTR:
				poller.unregister(server.fileno())
				poller = select.poll()
				poller.register(server.fileno(), select.POLLIN)

		except KeyboardInterrupt:
			print("KeyboardInterrupt")
			proc.terminate()
			proc.join()
			server.close()
			os.unlink(inotsock)
			sys.exit(1)

if __name__ == "__main__":
	multiprocessing.freeze_support()
	main()
