use std::collections::{BTreeMap, BTreeSet};

pub const MAX_ATTEMPTS: u32 = 3;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum JobStatus {
    Pending,
    Leased,
    Completed,
    Failed,
}

impl JobStatus {
    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Lease {
    pub worker_id: String,
    pub token: u32,
    pub expiry: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Job<P> {
    pub id: String,
    pub payload: P,
    pub status: JobStatus,
    pub attempts: u32,
    pub token: u32,
    pub lease: Option<Lease>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TransitionError {
    InvalidLeaseDuration,
    InvalidTimeDelta,
    TimeOverflow,
    JobAlreadyExists,
    NotFound,
    InvalidJobState,
    WorkerCrashed,
    LeaseExpired,
    LeaseOwnerMismatch,
    StaleFencingToken,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExpiredJob {
    pub job_id: String,
    pub status: JobStatus,
}

#[derive(Debug)]
pub struct Scheduler<P> {
    now: u64,
    lease_duration: u64,
    jobs: BTreeMap<String, Job<P>>,
    crashed_workers: BTreeSet<String>,
}

impl<P> Scheduler<P> {
    pub fn new(lease_duration: u64) -> Result<Self, TransitionError> {
        if lease_duration == 0 {
            return Err(TransitionError::InvalidLeaseDuration);
        }

        Ok(Self {
            now: 0,
            lease_duration,
            jobs: BTreeMap::new(),
            crashed_workers: BTreeSet::new(),
        })
    }

    pub fn now(&self) -> u64 {
        self.now
    }

    #[cfg(any(test, feature = "test-support"))]
    pub fn job(&self, job_id: &str) -> Option<&Job<P>> {
        self.jobs.get(job_id)
    }

    #[cfg(any(test, feature = "test-support"))]
    pub fn jobs(&self) -> impl Iterator<Item = &Job<P>> {
        self.jobs.values()
    }

    #[cfg(any(test, feature = "test-support"))]
    pub fn crashed_workers(&self) -> impl Iterator<Item = &str> {
        self.crashed_workers.iter().map(String::as_str)
    }

    pub fn submit(&mut self, job_id: String, payload: P) -> Result<&Job<P>, TransitionError> {
        if self.jobs.contains_key(&job_id) {
            return Err(TransitionError::JobAlreadyExists);
        }

        self.jobs.insert(
            job_id.clone(),
            Job {
                id: job_id.clone(),
                payload,
                status: JobStatus::Pending,
                attempts: 0,
                token: 0,
                lease: None,
            },
        );
        Ok(self.jobs.get(&job_id).expect("submitted job must exist"))
    }

    pub fn acquire(&mut self, worker_id: &str, job_id: &str) -> Result<Lease, TransitionError> {
        if self.crashed_workers.contains(worker_id) {
            return Err(TransitionError::WorkerCrashed);
        }

        let expiry = self
            .now
            .checked_add(self.lease_duration)
            .ok_or(TransitionError::TimeOverflow)?;
        let job = self.jobs.get_mut(job_id).ok_or(TransitionError::NotFound)?;
        if job.status != JobStatus::Pending || job.attempts >= MAX_ATTEMPTS {
            return Err(TransitionError::InvalidJobState);
        }

        job.attempts += 1;
        job.token += 1;
        job.status = JobStatus::Leased;
        let lease = Lease {
            worker_id: worker_id.to_owned(),
            token: job.token,
            expiry,
        };
        job.lease = Some(lease.clone());
        Ok(lease)
    }

    pub fn acquire_next(&mut self, worker_id: &str) -> Result<Option<&Job<P>>, TransitionError> {
        if self.crashed_workers.contains(worker_id) {
            return Err(TransitionError::WorkerCrashed);
        }

        let Some(job_id) = self
            .jobs
            .iter()
            .find(|(_, job)| job.status == JobStatus::Pending && job.attempts < MAX_ATTEMPTS)
            .map(|(job_id, _)| job_id.clone())
        else {
            return Ok(None);
        };

        self.acquire(worker_id, &job_id)?;
        Ok(self.jobs.get(&job_id))
    }

    pub fn complete(
        &mut self,
        worker_id: &str,
        job_id: &str,
        token: u32,
    ) -> Result<&Job<P>, TransitionError> {
        let job = self.jobs.get_mut(job_id).ok_or(TransitionError::NotFound)?;
        if job.status != JobStatus::Leased {
            return Err(TransitionError::InvalidJobState);
        }

        let lease = job.lease.as_ref().expect("leased job must have a lease");
        if self.now >= lease.expiry {
            return Err(TransitionError::LeaseExpired);
        }
        if lease.worker_id != worker_id {
            return Err(TransitionError::LeaseOwnerMismatch);
        }
        if lease.token != token {
            return Err(TransitionError::StaleFencingToken);
        }
        if self.crashed_workers.contains(worker_id) {
            return Err(TransitionError::WorkerCrashed);
        }

        job.status = JobStatus::Completed;
        job.lease = None;
        Ok(job)
    }

    #[cfg(any(test, feature = "test-support"))]
    pub fn crash(&mut self, worker_id: &str) {
        self.crashed_workers.insert(worker_id.to_owned());
    }

    pub fn advance_time(&mut self, delta: u64) -> Result<Vec<ExpiredJob>, TransitionError> {
        if delta == 0 {
            return Err(TransitionError::InvalidTimeDelta);
        }
        let new_now = self
            .now
            .checked_add(delta)
            .ok_or(TransitionError::TimeOverflow)?;
        self.now = new_now;

        let mut expired = Vec::new();
        let now = self.now;
        for job in self.jobs.values_mut() {
            let is_expired = job.status == JobStatus::Leased
                && job
                    .lease
                    .as_ref()
                    .map_or(false, |lease| now >= lease.expiry);
            if !is_expired {
                continue;
            }

            job.status = if job.attempts < MAX_ATTEMPTS {
                JobStatus::Pending
            } else {
                JobStatus::Failed
            };
            job.lease = None;
            expired.push(ExpiredJob {
                job_id: job.id.clone(),
                status: job.status,
            });
        }
        Ok(expired)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scheduler() -> Scheduler<&'static str> {
        Scheduler::new(2).unwrap()
    }

    #[test]
    fn submission_creates_pending_job_and_rejects_duplicate_id() {
        let mut scheduler = scheduler();

        let job = scheduler.submit("job-1".into(), "payload").unwrap();
        assert_eq!(job.status, JobStatus::Pending);
        assert_eq!(job.attempts, 0);
        assert_eq!(job.token, 0);
        assert_eq!(job.lease, None);

        assert_eq!(
            scheduler.submit("job-1".into(), "replacement"),
            Err(TransitionError::JobAlreadyExists)
        );
        assert_eq!(scheduler.job("job-1").unwrap().payload, "payload");
    }

    #[test]
    fn acquisition_creates_a_lease_and_increments_attempt_and_token() {
        let mut scheduler = scheduler();
        scheduler.submit("job-1".into(), "payload").unwrap();

        let lease = scheduler.acquire("worker-1", "job-1").unwrap();

        assert_eq!(lease.worker_id, "worker-1");
        assert_eq!(lease.token, 1);
        assert_eq!(lease.expiry, 2);
        let job = scheduler.job("job-1").unwrap();
        assert_eq!(job.status, JobStatus::Leased);
        assert_eq!(job.attempts, 1);
        assert_eq!(job.token, 1);
    }

    #[test]
    fn acquire_next_selects_pending_jobs_in_lexicographic_order() {
        let mut scheduler = scheduler();
        scheduler.submit("job-b".into(), "payload-b").unwrap();
        scheduler.submit("job-a".into(), "payload-a").unwrap();

        let acquired = scheduler.acquire_next("worker-1").unwrap().unwrap();

        assert_eq!(acquired.id, "job-a");
        assert_eq!(acquired.status, JobStatus::Leased);
        assert_eq!(scheduler.job("job-b").unwrap().status, JobStatus::Pending);
    }

    #[test]
    fn lease_expires_at_the_exact_expiry_boundary() {
        let mut scheduler = scheduler();
        scheduler.submit("job-1".into(), "payload").unwrap();
        scheduler.acquire("worker-1", "job-1").unwrap();

        assert!(scheduler.advance_time(1).unwrap().is_empty());
        assert_eq!(scheduler.job("job-1").unwrap().status, JobStatus::Leased);

        let expired = scheduler.advance_time(1).unwrap();
        assert_eq!(
            expired,
            vec![ExpiredJob {
                job_id: "job-1".into(),
                status: JobStatus::Pending,
            }]
        );
        assert_eq!(scheduler.job("job-1").unwrap().status, JobStatus::Pending);
    }

    #[test]
    fn third_expired_attempt_exhausts_retries_and_fails_job() {
        let mut scheduler = scheduler();
        scheduler.submit("job-1".into(), "payload").unwrap();

        for attempt in 1..=MAX_ATTEMPTS {
            let lease = scheduler.acquire("worker-1", "job-1").unwrap();
            assert_eq!(lease.token, attempt);
            scheduler.advance_time(2).unwrap();
        }

        let job = scheduler.job("job-1").unwrap();
        assert_eq!(job.status, JobStatus::Failed);
        assert_eq!(job.attempts, MAX_ATTEMPTS);
        assert_eq!(
            scheduler.acquire("worker-1", "job-1"),
            Err(TransitionError::InvalidJobState)
        );
    }

    #[test]
    fn terminal_states_are_monotonic() {
        let mut completed = scheduler();
        completed.submit("job-1".into(), "payload").unwrap();
        let lease = completed.acquire("worker-1", "job-1").unwrap();
        completed
            .complete("worker-1", "job-1", lease.token)
            .unwrap();
        assert_eq!(completed.advance_time(10).unwrap(), Vec::new());
        assert_eq!(
            completed.acquire("worker-1", "job-1"),
            Err(TransitionError::InvalidJobState)
        );
        assert_eq!(completed.job("job-1").unwrap().status, JobStatus::Completed);

        let mut failed = scheduler();
        failed.submit("job-1".into(), "payload").unwrap();
        for _ in 0..MAX_ATTEMPTS {
            failed.acquire("worker-1", "job-1").unwrap();
            failed.advance_time(2).unwrap();
        }
        assert_eq!(
            failed.complete("worker-1", "job-1", 3),
            Err(TransitionError::InvalidJobState)
        );
        assert_eq!(failed.job("job-1").unwrap().status, JobStatus::Failed);
    }

    #[test]
    fn stale_fencing_token_is_rejected_without_mutation() {
        let mut scheduler = Scheduler::new(1).unwrap();
        scheduler.submit("job-1".into(), "payload").unwrap();
        scheduler.acquire("worker-1", "job-1").unwrap();
        scheduler.advance_time(1).unwrap();
        let current = scheduler.acquire("worker-2", "job-1").unwrap();

        assert_eq!(
            scheduler.complete("worker-2", "job-1", 1),
            Err(TransitionError::StaleFencingToken)
        );
        let job = scheduler.job("job-1").unwrap();
        assert_eq!(job.status, JobStatus::Leased);
        assert_eq!(job.lease.as_ref(), Some(&current));

        assert_eq!(
            scheduler.complete("worker-1", "job-1", 1),
            Err(TransitionError::LeaseOwnerMismatch)
        );
        assert_eq!(scheduler.job("job-1").unwrap().status, JobStatus::Leased);
    }

    #[test]
    fn crashed_worker_cannot_acquire_or_complete() {
        let mut scheduler = scheduler();
        scheduler.submit("job-1".into(), "payload").unwrap();
        scheduler.crash("worker-1");
        assert_eq!(
            scheduler.acquire("worker-1", "job-1"),
            Err(TransitionError::WorkerCrashed)
        );

        let lease = scheduler.acquire("worker-2", "job-1").unwrap();
        scheduler.crash("worker-2");
        assert_eq!(
            scheduler.complete("worker-2", "job-1", lease.token),
            Err(TransitionError::WorkerCrashed)
        );
        assert_eq!(scheduler.job("job-1").unwrap().status, JobStatus::Leased);
    }
}
