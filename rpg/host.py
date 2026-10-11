"""Hosts one game for several front ends at once: the terminal, the web viewer
and send.py all put lines on one queue, a single worker plays them in order
(one turn at a time, since the model is one GPU), and every output event goes
to every listener and to events.jsonl in the run folder."""
import json
import queue
import threading


class Job:
    def __init__(self, line, source):
        self.line, self.source = line, source
        self.first = self.last = None   # event range this line produced
        self.keep_going = True          # False after /quit
        self.done = threading.Event()


class Host:
    def __init__(self, game, run_dir):
        self.game, self.run_dir = game, run_dir
        self.events, self.listeners = [], []
        self.lock = threading.Lock()
        self.jobs = queue.Queue()
        path = run_dir / "events.jsonl"
        if path.exists():  # --continue: the viewer shows the earlier turns too
            self.events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        self._file = path.open("a", encoding="utf-8")
        game.out = self.publish
        threading.Thread(target=self._work, daemon=True).start()

    def publish(self, ev):
        with self.lock:
            ev["seq"] = len(self.events)
            self.events.append(ev)
            self._file.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self._file.flush()
            listeners = list(self.listeners)
        for fn in listeners:
            try:
                fn(ev)
            except Exception:  # a closed browser tab must not stop the game
                pass

    def subscribe(self, fn, since=0):
        """Add a listener; returns the events from `since` it missed."""
        with self.lock:
            self.listeners.append(fn)
            return self.events[since:]

    def unsubscribe(self, fn):
        with self.lock:
            if fn in self.listeners:
                self.listeners.remove(fn)

    def submit(self, line, source):
        job = Job(line, source)
        self.jobs.put(job)
        if self.jobs.qsize() > 1 or self.busy:
            self.publish({"kind": "queued", "text": line, "source": source})
        return job

    busy = False

    def _work(self):
        while True:
            job = self.jobs.get()
            self.busy = True
            job.first = len(self.events)
            self.publish({"kind": "busy", "text": job.line, "source": job.source})
            try:
                job.keep_going = self.game.handle(job.line, job.source)
                if not job.keep_going and job.source != "terminal":
                    self.game.emit("info", "  /quit only ends the game from the terminal")
            except Exception as e:  # report it and keep hosting
                self.game.emit("error", f"  {type(e).__name__}: {e}")
            self.busy = False
            self.publish({"kind": "idle", "text": ""})
            job.last = len(self.events)
            job.done.set()
