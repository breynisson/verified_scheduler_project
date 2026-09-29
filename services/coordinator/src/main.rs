use scheduler_core::{Job, JobStatus, Scheduler, TransitionError};
use serde_json::{json, Map, Value};
use std::env;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};

const LEASE_DURATION: u64 = 1;
const MAX_REQUEST_BYTES: usize = 64 * 1024;

struct Config {
    bind_address: String,
    test_mode: bool,
}

struct HttpRequest {
    method: String,
    path: String,
    body: Vec<u8>,
}

struct HttpResponse {
    status: &'static str,
    body: Option<String>,
}

impl HttpResponse {
    fn json(status: &'static str, value: Value) -> Self {
        Self {
            status,
            body: Some(value.to_string()),
        }
    }

    fn empty(status: &'static str) -> Self {
        Self { status, body: None }
    }

    fn error(status: &'static str, code: &'static str, message: &'static str) -> Self {
        Self::json(status, json!({"error": {"code": code, "message": message}}))
    }
}

struct Coordinator {
    scheduler: Scheduler<Value>,
    test_mode: bool,
}

impl Coordinator {
    fn new(test_mode: bool) -> Self {
        Self {
            scheduler: Scheduler::new(LEASE_DURATION).expect("lease duration must be positive"),
            test_mode,
        }
    }

    fn route(&mut self, request: HttpRequest) -> HttpResponse {
        if request.path == "/health" {
            return if request.method == "GET" {
                HttpResponse::json("200 OK", json!({"status": "ok"}))
            } else {
                method_not_allowed()
            };
        }

        if request.path == "/jobs" {
            return if request.method == "POST" {
                self.submit(&request.body)
            } else {
                method_not_allowed()
            };
        }

        if let Some(worker_id) = path_parameter(&request.path, "/workers/", "/acquire") {
            return if request.method == "POST" {
                self.acquire(worker_id)
            } else {
                method_not_allowed()
            };
        }

        if let Some(job_id) = path_parameter(&request.path, "/jobs/", "/complete") {
            return if request.method == "POST" {
                self.complete(job_id, &request.body)
            } else {
                method_not_allowed()
            };
        }

        if request.path == "/test/advance-time" {
            return if request.method != "POST" {
                method_not_allowed()
            } else if !self.test_mode {
                test_mode_disabled()
            } else {
                self.advance_time(&request.body)
            };
        }

        if let Some(worker_id) = path_parameter(&request.path, "/test/workers/", "/crash") {
            return if request.method != "POST" {
                method_not_allowed()
            } else if !self.test_mode {
                test_mode_disabled()
            } else {
                self.scheduler.crash(worker_id);
                HttpResponse::empty("204 No Content")
            };
        }

        if request.path == "/debug/state" {
            return if request.method != "GET" {
                method_not_allowed()
            } else if !self.test_mode {
                test_mode_disabled()
            } else {
                self.debug_state()
            };
        }

        HttpResponse::error("404 Not Found", "not_found", "route not found")
    }

    fn submit(&mut self, body: &[u8]) -> HttpResponse {
        let object = match json_object(body) {
            Ok(object) => object,
            Err(response) => return response,
        };
        let Some(job_id) = nonempty_string(&object, "id") else {
            return invalid_request("id must be a non-empty string");
        };
        let Some(payload) = object.get("payload").filter(|value| value.is_object()) else {
            return invalid_request("payload must be an object");
        };

        match self.scheduler.submit(job_id.to_owned(), payload.clone()) {
            Ok(job) => HttpResponse::json("201 Created", job_json(job)),
            Err(error) => transition_error(error),
        }
    }

    fn acquire(&mut self, worker_id: &str) -> HttpResponse {
        match self.scheduler.acquire_next(worker_id) {
            Ok(Some(job)) => HttpResponse::json("200 OK", job_json(job)),
            Ok(None) => HttpResponse::empty("204 No Content"),
            Err(error) => transition_error(error),
        }
    }

    fn complete(&mut self, job_id: &str, body: &[u8]) -> HttpResponse {
        let object = match json_object(body) {
            Ok(object) => object,
            Err(response) => return response,
        };
        let Some(worker_id) = nonempty_string(&object, "worker_id") else {
            return invalid_request("worker_id must be a non-empty string");
        };
        let Some(token) = object
            .get("token")
            .and_then(Value::as_u64)
            .filter(|token| *token > 0 && *token <= u32::MAX.into())
        else {
            return invalid_request("token must be a positive integer");
        };

        match self.scheduler.complete(worker_id, job_id, token as u32) {
            Ok(job) => HttpResponse::json("200 OK", job_json(job)),
            Err(error) => transition_error(error),
        }
    }

    fn advance_time(&mut self, body: &[u8]) -> HttpResponse {
        let object = match json_object(body) {
            Ok(object) => object,
            Err(response) => return response,
        };
        let Some(delta) = object
            .get("delta")
            .and_then(Value::as_u64)
            .filter(|delta| *delta > 0)
        else {
            return invalid_request("delta must be a positive integer");
        };

        match self.scheduler.advance_time(delta) {
            Ok(expired) => HttpResponse::json(
                "200 OK",
                json!({
                    "now": self.scheduler.now(),
                    "expired": expired
                        .into_iter()
                        .map(|job| json!({
                            "job_id": job.job_id,
                            "status": status_name(job.status),
                        }))
                        .collect::<Vec<_>>(),
                }),
            ),
            Err(error) => transition_error(error),
        }
    }

    fn debug_state(&self) -> HttpResponse {
        HttpResponse::json(
            "200 OK",
            json!({
                "now": self.scheduler.now(),
                "jobs": self.scheduler.jobs().map(job_json).collect::<Vec<_>>(),
                "crashed_workers": self.scheduler.crashed_workers().collect::<Vec<_>>(),
            }),
        )
    }
}

fn main() -> std::io::Result<()> {
    let config = parse_config()?;
    let listener = TcpListener::bind(&config.bind_address)?;
    let mut coordinator = Coordinator::new(config.test_mode);

    println!("LISTENING {}", listener.local_addr()?);
    std::io::stdout().flush()?;

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => handle_connection(stream, &mut coordinator)?,
            Err(error) => eprintln!("failed to accept connection: {}", error),
        }
    }

    Ok(())
}

fn parse_config() -> std::io::Result<Config> {
    let mut bind_address = "127.0.0.1:8080".to_owned();
    let mut test_mode = false;
    let mut args = env::args().skip(1);

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--bind" => bind_address = args.next().ok_or_else(usage_error)?,
            "--test-mode" => test_mode = true,
            _ => return Err(usage_error()),
        }
    }

    Ok(Config {
        bind_address,
        test_mode,
    })
}

fn usage_error() -> std::io::Error {
    std::io::Error::new(
        std::io::ErrorKind::InvalidInput,
        "usage: coordinator [--bind ADDRESS] [--test-mode]",
    )
}

fn handle_connection(mut stream: TcpStream, coordinator: &mut Coordinator) -> std::io::Result<()> {
    let response = match read_request(&mut stream) {
        Ok(request) => coordinator.route(request),
        Err(()) => invalid_request("malformed HTTP request"),
    };
    write_response(&mut stream, response)
}

fn read_request(stream: &mut TcpStream) -> Result<HttpRequest, ()> {
    let mut bytes = Vec::new();
    let header_end = loop {
        if bytes.len() >= MAX_REQUEST_BYTES {
            return Err(());
        }
        let mut chunk = [0_u8; 4096];
        let count = stream.read(&mut chunk).map_err(|_| ())?;
        if count == 0 {
            return Err(());
        }
        bytes.extend_from_slice(&chunk[..count]);
        if let Some(position) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            break position + 4;
        }
    };

    let headers = std::str::from_utf8(&bytes[..header_end]).map_err(|_| ())?;
    let mut lines = headers.split("\r\n");
    let mut request_line = lines.next().ok_or(())?.split_whitespace();
    let method = request_line.next().ok_or(())?.to_owned();
    let path = request_line.next().ok_or(())?.to_owned();
    if request_line.next() != Some("HTTP/1.1") || request_line.next().is_some() {
        return Err(());
    }

    let mut content_length = 0_usize;
    for line in lines.filter(|line| !line.is_empty()) {
        let Some((name, value)) = line.split_once(':') else {
            return Err(());
        };
        if name.eq_ignore_ascii_case("content-length") {
            content_length = value.trim().parse().map_err(|_| ())?;
        }
    }
    if header_end + content_length > MAX_REQUEST_BYTES {
        return Err(());
    }
    while bytes.len() < header_end + content_length {
        let mut chunk = [0_u8; 4096];
        let count = stream.read(&mut chunk).map_err(|_| ())?;
        if count == 0 || bytes.len() + count > MAX_REQUEST_BYTES {
            return Err(());
        }
        bytes.extend_from_slice(&chunk[..count]);
    }

    Ok(HttpRequest {
        method,
        path,
        body: bytes[header_end..header_end + content_length].to_vec(),
    })
}

fn write_response(stream: &mut TcpStream, response: HttpResponse) -> std::io::Result<()> {
    let body = response.body.unwrap_or_default();
    let content_type = if body.is_empty() {
        ""
    } else {
        "Content-Type: application/json\r\n"
    };
    let response = format!(
        "HTTP/1.1 {}\r\n{}Content-Length: {}\r\nConnection: close\r\n\r\n{}",
        response.status,
        content_type,
        body.len(),
        body
    );
    stream.write_all(response.as_bytes())
}

fn json_object(body: &[u8]) -> Result<Map<String, Value>, HttpResponse> {
    serde_json::from_slice::<Value>(body)
        .ok()
        .and_then(|value| value.as_object().cloned())
        .ok_or_else(|| invalid_request("body must be a JSON object"))
}

fn nonempty_string<'a>(object: &'a Map<String, Value>, name: &str) -> Option<&'a str> {
    object
        .get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn path_parameter<'a>(path: &'a str, prefix: &str, suffix: &str) -> Option<&'a str> {
    path.strip_prefix(prefix)
        .and_then(|rest| rest.strip_suffix(suffix))
        .filter(|value| !value.is_empty() && !value.contains('/'))
}

fn job_json(job: &Job<Value>) -> Value {
    json!({
        "id": &job.id,
        "payload": &job.payload,
        "status": status_name(job.status),
        "attempts": job.attempts,
        "lease": job.lease.as_ref().map(|lease| json!({
            "worker_id": &lease.worker_id,
            "token": lease.token,
            "expiry": lease.expiry,
        })),
    })
}

fn status_name(status: JobStatus) -> &'static str {
    match status {
        JobStatus::Pending => "pending",
        JobStatus::Leased => "leased",
        JobStatus::Completed => "completed",
        JobStatus::Failed => "failed",
    }
}

fn transition_error(error: TransitionError) -> HttpResponse {
    match error {
        TransitionError::JobAlreadyExists => {
            HttpResponse::error("409 Conflict", "job_already_exists", "job already exists")
        }
        TransitionError::NotFound => {
            HttpResponse::error("404 Not Found", "not_found", "job not found")
        }
        TransitionError::InvalidJobState => HttpResponse::error(
            "409 Conflict",
            "invalid_job_state",
            "operation is not allowed for the current job state",
        ),
        TransitionError::WorkerCrashed => {
            HttpResponse::error("409 Conflict", "worker_crashed", "worker is crashed")
        }
        TransitionError::LeaseExpired => {
            HttpResponse::error("409 Conflict", "lease_expired", "lease has expired")
        }
        TransitionError::LeaseOwnerMismatch => HttpResponse::error(
            "409 Conflict",
            "lease_owner_mismatch",
            "worker does not own the current lease",
        ),
        TransitionError::StaleFencingToken => HttpResponse::error(
            "409 Conflict",
            "stale_fencing_token",
            "fencing token is not current",
        ),
        TransitionError::InvalidLeaseDuration
        | TransitionError::InvalidTimeDelta
        | TransitionError::TimeOverflow => invalid_request("invalid logical time value"),
    }
}

fn invalid_request(message: &'static str) -> HttpResponse {
    HttpResponse::error("400 Bad Request", "invalid_request", message)
}

fn method_not_allowed() -> HttpResponse {
    HttpResponse::error(
        "405 Method Not Allowed",
        "method_not_allowed",
        "method not allowed",
    )
}

fn test_mode_disabled() -> HttpResponse {
    HttpResponse::error(
        "503 Service Unavailable",
        "test_mode_disabled",
        "test mode is disabled",
    )
}
