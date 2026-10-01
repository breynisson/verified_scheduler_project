use rusqlite::{params, Connection};
use scheduler_core::{Job, JobStatus, Lease, Scheduler, SchedulerState};
use serde_json::{json, Value};
use std::convert::TryFrom;
use std::error::Error;
use std::io;
use std::path::Path;

pub struct PersistentState {
    pub scheduler: Scheduler<Value>,
    pub commits: Vec<Value>,
}

pub struct StateStore {
    connection: Connection,
    fail_next_save: bool,
    fail_next_save_after_write: bool,
}

impl StateStore {
    pub fn open(path: Option<&Path>) -> Result<Self, Box<dyn Error>> {
        let connection = match path {
            Some(path) => Connection::open(path)?,
            None => Connection::open_in_memory()?,
        };
        connection.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = FULL;
             CREATE TABLE IF NOT EXISTS coordinator_state (
                 singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                 schema_version INTEGER NOT NULL,
                 state_json TEXT NOT NULL
             );",
        )?;
        Ok(Self {
            connection,
            fail_next_save: false,
            fail_next_save_after_write: false,
        })
    }

    pub fn load(
        &self,
        expected_lease_duration: u64,
    ) -> Result<Option<PersistentState>, Box<dyn Error>> {
        let mut statement = self.connection.prepare(
            "SELECT schema_version, state_json FROM coordinator_state WHERE singleton = 1",
        )?;
        let mut rows = statement.query([])?;
        let Some(row) = rows.next()? else {
            return Ok(None);
        };
        let version: u32 = row.get(0)?;
        if version != 1 {
            return Err(invalid_data("unsupported coordinator state schema"));
        }
        let encoded: String = row.get(1)?;
        decode_state(&encoded, expected_lease_duration).map(Some)
    }

    pub fn save(
        &mut self,
        scheduler: &Scheduler<Value>,
        commits: &[Value],
    ) -> Result<(), Box<dyn Error>> {
        if self.fail_next_save {
            self.fail_next_save = false;
            return Err(invalid_data("injected persistence failure"));
        }
        let fail_after_write = std::mem::take(&mut self.fail_next_save_after_write);
        let encoded = encode_state(scheduler.snapshot(), commits)?;
        let transaction = self.connection.transaction()?;
        transaction.execute(
            "INSERT INTO coordinator_state (singleton, schema_version, state_json)
             VALUES (1, 1, ?1)
             ON CONFLICT(singleton) DO UPDATE SET
                 schema_version = excluded.schema_version,
                 state_json = excluded.state_json",
            params![encoded],
        )?;
        if fail_after_write {
            return Err(invalid_data(
                "injected persistence failure after transactional write",
            ));
        }
        transaction.commit()?;
        Ok(())
    }

    pub fn fail_next_save(&mut self) {
        self.fail_next_save = true;
    }

    pub fn fail_next_save_after_write(&mut self) {
        self.fail_next_save_after_write = true;
    }
}

fn encode_state(state: SchedulerState<Value>, commits: &[Value]) -> Result<String, Box<dyn Error>> {
    let jobs = state
        .jobs
        .into_iter()
        .map(|job| {
            json!({
                "id": job.id,
                "payload": job.payload,
                "status": status_name(job.status),
                "attempts": job.attempts,
                "token": job.token,
                "lease": job.lease.map(|lease| json!({
                    "worker_id": lease.worker_id,
                    "token": lease.token,
                    "expiry": lease.expiry,
                })),
            })
        })
        .collect::<Vec<_>>();
    Ok(serde_json::to_string(&json!({
        "now": state.now,
        "lease_duration": state.lease_duration,
        "jobs": jobs,
        "crashed_workers": state.crashed_workers,
        "commits": commits,
    }))?)
}

fn decode_state(
    encoded: &str,
    expected_lease_duration: u64,
) -> Result<PersistentState, Box<dyn Error>> {
    let value: Value = serde_json::from_str(encoded)?;
    let object = value
        .as_object()
        .ok_or_else(|| invalid_data("persisted state must be an object"))?;
    let now = unsigned(object.get("now"), "now")?;
    let lease_duration = unsigned(object.get("lease_duration"), "lease_duration")?;
    if lease_duration != expected_lease_duration {
        return Err(invalid_data(
            "persisted lease duration does not match coordinator policy",
        ));
    }
    let jobs = object
        .get("jobs")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid_data("persisted jobs must be an array"))?
        .iter()
        .map(decode_job)
        .collect::<Result<Vec<_>, _>>()?;
    let crashed_workers = object
        .get("crashed_workers")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid_data("persisted crashed workers must be an array"))?
        .iter()
        .map(|worker| {
            worker
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| invalid_data("persisted worker ID must be a string"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let commits = object
        .get("commits")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid_data("persisted commits must be an array"))?
        .clone();
    let scheduler = Scheduler::from_state(SchedulerState {
        now,
        lease_duration,
        jobs,
        crashed_workers,
    })
    .map_err(|_| invalid_data("persisted scheduler state is invalid"))?;
    Ok(PersistentState { scheduler, commits })
}

fn decode_job(value: &Value) -> Result<Job<Value>, Box<dyn Error>> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid_data("persisted job must be an object"))?;
    let id = object
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| invalid_data("persisted job ID must be a string"))?
        .to_owned();
    let payload = object
        .get("payload")
        .filter(|payload| payload.is_object())
        .ok_or_else(|| invalid_data("persisted payload must be an object"))?
        .clone();
    let status = match object.get("status").and_then(Value::as_str) {
        Some("pending") => JobStatus::Pending,
        Some("leased") => JobStatus::Leased,
        Some("completed") => JobStatus::Completed,
        Some("failed") => JobStatus::Failed,
        _ => return Err(invalid_data("persisted job status is invalid")),
    };
    let attempts = u32::try_from(unsigned(object.get("attempts"), "attempts")?)?;
    let token = u32::try_from(unsigned(object.get("token"), "token")?)?;
    let lease = match object.get("lease") {
        Some(Value::Null) | None => None,
        Some(value) => {
            let lease = value
                .as_object()
                .ok_or_else(|| invalid_data("persisted lease must be an object"))?;
            Some(Lease {
                worker_id: lease
                    .get("worker_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| invalid_data("persisted lease worker must be a string"))?
                    .to_owned(),
                token: u32::try_from(unsigned(lease.get("token"), "lease token")?)?,
                expiry: unsigned(lease.get("expiry"), "lease expiry")?,
            })
        }
    };
    Ok(Job {
        id,
        payload,
        status,
        attempts,
        token,
        lease,
    })
}

fn unsigned(value: Option<&Value>, name: &str) -> Result<u64, Box<dyn Error>> {
    value
        .and_then(Value::as_u64)
        .ok_or_else(|| invalid_data(&format!("persisted {name} must be an unsigned integer")))
}

fn status_name(status: JobStatus) -> &'static str {
    match status {
        JobStatus::Pending => "pending",
        JobStatus::Leased => "leased",
        JobStatus::Completed => "completed",
        JobStatus::Failed => "failed",
    }
}

fn invalid_data(message: &str) -> Box<dyn Error> {
    Box::new(io::Error::new(
        io::ErrorKind::InvalidData,
        message.to_owned(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sqlite_round_trip_preserves_scheduler_and_commits() {
        let mut store = StateStore::open(None).unwrap();
        let mut scheduler = Scheduler::new(1).unwrap();
        scheduler
            .submit("job-1".into(), json!({"task": "example"}))
            .unwrap();
        scheduler.acquire("worker-1", "job-1").unwrap();
        let commits = vec![json!({
            "job_id": "historical-job",
            "worker_id": "worker-2",
            "token": 1,
            "accepted_at": 0,
            "expiry": 1,
        })];

        store.save(&scheduler, &commits).unwrap();
        let restored = store.load(1).unwrap().unwrap();

        assert_eq!(restored.scheduler.snapshot(), scheduler.snapshot());
        assert_eq!(restored.commits, commits);
    }

    #[test]
    fn load_rejects_persisted_lease_duration_that_changes_policy() {
        let mut store = StateStore::open(None).unwrap();
        let scheduler = Scheduler::new(2).unwrap();

        store.save(&scheduler, &[]).unwrap();

        assert!(store.load(1).is_err());
    }
}
