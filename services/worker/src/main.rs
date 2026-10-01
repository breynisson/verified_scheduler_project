use serde_json::{json, Value};
use std::env;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

struct Config {
    coordinator: String,
    worker_id: String,
    once: bool,
    poll_interval: Duration,
    pause_before_complete: Duration,
    start_gate: Option<PathBuf>,
}

struct Response {
    status: u16,
    body: Option<Value>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = parse_config()?;
    if let Some(gate) = &config.start_gate {
        let deadline = Instant::now() + Duration::from_secs(30);
        while !gate.exists() {
            if Instant::now() >= deadline {
                return Err("timed out waiting for test start gate".into());
            }
            thread::sleep(Duration::from_millis(10));
        }
    }
    loop {
        let acquired = request(
            &config.coordinator,
            "POST",
            &format!("/workers/{}/acquire", config.worker_id),
            None,
        )?;
        match acquired.status {
            200 => {
                let body = acquired.body.ok_or("acquire response had no body")?;
                let job_id = body
                    .get("id")
                    .and_then(Value::as_str)
                    .ok_or("acquire response had no job ID")?;
                let token = body
                    .pointer("/lease/token")
                    .and_then(Value::as_u64)
                    .ok_or("acquire response had no fencing token")?;
                println!("ACQUIRED {} {} {}", config.worker_id, job_id, token);
                if !config.pause_before_complete.is_zero() {
                    thread::sleep(config.pause_before_complete);
                }
                let completed = request(
                    &config.coordinator,
                    "POST",
                    &format!("/jobs/{job_id}/complete"),
                    Some(json!({"worker_id": config.worker_id, "token": token})),
                )?;
                if completed.status != 200 {
                    return Err(format!("completion failed with HTTP {}", completed.status).into());
                }
                println!("COMPLETED {} {} {}", config.worker_id, job_id, token);
            }
            204 => println!("NO_JOB {}", config.worker_id),
            status => return Err(format!("acquisition failed with HTTP {status}").into()),
        }

        if config.once {
            return Ok(());
        }
        thread::sleep(config.poll_interval);
    }
}

fn parse_config() -> Result<Config, Box<dyn std::error::Error>> {
    let mut coordinator = None;
    let mut worker_id = None;
    let mut once = false;
    let mut test_mode = false;
    let mut poll_ms = 100_u64;
    let mut pause_ms = 0_u64;
    let mut start_gate = None;
    let mut args = env::args().skip(1);

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--coordinator" => coordinator = args.next(),
            "--id" => worker_id = args.next(),
            "--once" => once = true,
            "--test-mode" => test_mode = true,
            "--poll-ms" => poll_ms = args.next().ok_or_else(usage)?.parse()?,
            "--test-pause-before-complete-ms" => {
                pause_ms = args.next().ok_or_else(usage)?.parse()?
            }
            "--test-start-gate" => start_gate = Some(PathBuf::from(args.next().ok_or_else(usage)?)),
            _ => return Err(usage().into()),
        }
    }

    if (pause_ms > 0 || start_gate.is_some()) && !test_mode {
        return Err(usage().into());
    }

    Ok(Config {
        coordinator: coordinator.ok_or_else(usage)?,
        worker_id: worker_id.filter(|id| !id.is_empty()).ok_or_else(usage)?,
        once,
        poll_interval: Duration::from_millis(poll_ms),
        pause_before_complete: Duration::from_millis(pause_ms),
        start_gate,
    })
}

fn usage() -> &'static str {
    "usage: worker --coordinator HOST:PORT --id ID [--once] [--poll-ms N] [--test-mode [--test-pause-before-complete-ms N] [--test-start-gate PATH]]"
}

fn request(
    address: &str,
    method: &str,
    path: &str,
    body: Option<Value>,
) -> Result<Response, Box<dyn std::error::Error>> {
    let mut stream = TcpStream::connect(address)?;
    let encoded = body.map(|value| value.to_string()).unwrap_or_default();
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{encoded}",
        encoded.len()
    );
    stream.write_all(request.as_bytes())?;

    let mut response = String::new();
    stream.read_to_string(&mut response)?;
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .ok_or("malformed HTTP response")?;
    let status = headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .ok_or("malformed HTTP status")?
        .parse()?;
    let body = if body.is_empty() {
        None
    } else {
        Some(serde_json::from_str(body)?)
    };
    Ok(Response { status, body })
}
