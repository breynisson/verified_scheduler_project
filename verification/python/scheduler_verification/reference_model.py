from copy import deepcopy

from .adapter import Outcome


MAX_ATTEMPTS = 3
LEASE_DURATION = 1


def error(status, code):
    return Outcome(status, {"error": {"code": code}})


class ReferenceScheduler:
    def __init__(self):
        self.now = 0
        self.jobs = {}
        self.crashed_workers = set()
        self.commits = []

    def submit(self, job_id, payload):
        if job_id in self.jobs:
            return error(409, "job_already_exists")
        job = {
            "id": job_id,
            "payload": deepcopy(payload),
            "status": "pending",
            "attempts": 0,
            "token": 0,
            "lease": None,
        }
        self.jobs[job_id] = job
        return Outcome(201, self._visible_job(job))

    def acquire(self, worker_id):
        if worker_id in self.crashed_workers:
            return error(409, "worker_crashed")
        eligible = sorted(
            job_id
            for job_id, job in self.jobs.items()
            if job["status"] == "pending" and job["attempts"] < MAX_ATTEMPTS
        )
        if not eligible:
            return Outcome(204, None)
        job = self.jobs[eligible[0]]
        job["attempts"] += 1
        job["token"] += 1
        job["status"] = "leased"
        job["lease"] = {
            "worker_id": worker_id,
            "token": job["token"],
            "expiry": self.now + LEASE_DURATION,
        }
        return Outcome(200, self._visible_job(job))

    def complete(self, worker_id, job_id, token):
        job = self.jobs.get(job_id)
        if job is None:
            return error(404, "not_found")
        if job["status"] != "leased":
            return error(409, "invalid_job_state")
        lease = job["lease"]
        if self.now >= lease["expiry"]:
            return error(409, "lease_expired")
        if worker_id != lease["worker_id"]:
            return error(409, "lease_owner_mismatch")
        if token != lease["token"]:
            return error(409, "stale_fencing_token")
        if worker_id in self.crashed_workers:
            return error(409, "worker_crashed")
        self.commits.append(
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "token": token,
                "accepted_at": self.now,
                "expiry": lease["expiry"],
            }
        )
        job["status"] = "completed"
        job["lease"] = None
        return Outcome(200, self._visible_job(job))

    def crash(self, worker_id):
        self.crashed_workers.add(worker_id)
        return Outcome(204, None)

    def advance_time(self, delta):
        self.now += delta
        expired = []
        for job_id in sorted(self.jobs):
            job = self.jobs[job_id]
            if job["status"] != "leased" or self.now < job["lease"]["expiry"]:
                continue
            job["status"] = (
                "pending" if job["attempts"] < MAX_ATTEMPTS else "failed"
            )
            job["lease"] = None
            expired.append({"job_id": job_id, "status": job["status"]})
        return Outcome(200, {"now": self.now, "expired": expired})

    def state(self):
        return {
            "now": self.now,
            "jobs": [self._visible_job(self.jobs[job_id]) for job_id in sorted(self.jobs)],
            "crashed_workers": sorted(self.crashed_workers),
            "commits": deepcopy(self.commits),
        }

    @staticmethod
    def _visible_job(job):
        return {
            "id": job["id"],
            "payload": deepcopy(job["payload"]),
            "status": job["status"],
            "attempts": job["attempts"],
            "lease": deepcopy(job["lease"]),
        }
