use std::env;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};

fn main() -> std::io::Result<()> {
    let bind_address = parse_bind_address()?;
    let listener = TcpListener::bind(&bind_address)?;

    println!("LISTENING {}", listener.local_addr()?);
    std::io::stdout().flush()?;

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => handle_connection(stream)?,
            Err(error) => eprintln!("failed to accept connection: {}", error),
        }
    }

    Ok(())
}

fn parse_bind_address() -> std::io::Result<String> {
    let mut args = env::args().skip(1);
    match (args.next(), args.next(), args.next()) {
        (None, None, None) => Ok("127.0.0.1:8080".to_owned()),
        (Some(flag), Some(address), None) if flag == "--bind" => Ok(address),
        _ => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "usage: coordinator [--bind ADDRESS]",
        )),
    }
}

fn handle_connection(mut stream: TcpStream) -> std::io::Result<()> {
    let mut request = [0_u8; 4096];
    let bytes_read = stream.read(&mut request)?;
    let request_line = String::from_utf8_lossy(&request[..bytes_read])
        .lines()
        .next()
        .unwrap_or("")
        .to_owned();

    let (status, body) = match request_line.as_str() {
        line if line.starts_with("GET /health ") => ("200 OK", r#"{"status":"ok"}"#),
        line if line.contains(" /health ") => (
            "405 Method Not Allowed",
            r#"{"error":{"code":"method_not_allowed","message":"method not allowed"}}"#,
        ),
        _ => (
            "404 Not Found",
            r#"{"error":{"code":"not_found","message":"route not found"}}"#,
        ),
    };
    let response = format!(
        "HTTP/1.1 {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        status,
        body.len(),
        body
    );

    stream.write_all(response.as_bytes())
}
