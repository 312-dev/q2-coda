//! q2-iroh: a spike of Car Remote across networks (docs/remote.md#car-remote-across-networks).
//!
//! On the Q2, `serve` keeps an iroh endpoint registered with n0's public relay and turns each
//! incoming QUIC stream into a local TCP connection to coda-rc (127.0.0.1:7720), so coda-rc
//! itself is unchanged. On the phone's side (here, a laptop), `dial` listens on a local TCP port
//! and carries each connection to the Q2 as one stream, found by its endpoint id alone: iroh
//! starts through the relay and moves to a direct, hole-punched path when one opens.
//! `bench` sends small messages down one stream and times their echoes.
//!
//!   q2-iroh serve [--key FILE] [--forward ADDR] [--allow ID]...
//!   q2-iroh dial ID [--listen ADDR] [--relay-only]
//!   q2-iroh bench ID [--count N] [--size BYTES] [--relay-only]

use std::{net::SocketAddr, str::FromStr, time::Instant};

use anyhow::{bail, Context, Result};
use iroh::{
    endpoint::{presets, Connection},
    Endpoint, EndpointId, SecretKey,
};
use tokio::{
    io::AsyncWriteExt,
    net::{TcpListener, TcpStream},
};

/// The ALPN both sides must name; a peer speaking anything else is refused in the handshake.
const ALPN: &[u8] = b"q2muse/coda-rc/0";

/// One thread: no tokio worker pool on a 128 MB device.
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("serve") => serve(&args[1..]).await,
        Some("dial") => dial(&args[1..]).await,
        Some("bench") => bench(&args[1..]).await,
        _ => bail!("usage: q2-iroh serve|dial|bench ... (see the source's header)"),
    }
}

fn opt<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).map(String::as_str)
}

fn opts<'a>(args: &'a [String], name: &str) -> Vec<&'a str> {
    args.windows(2).filter(|w| w[0] == name).map(|w| w[1].as_str()).collect()
}

/// The endpoint's identity, kept in FILE so the Q2's id survives a restart; made on first use.
fn load_key(path: &str) -> Result<SecretKey> {
    match std::fs::read(path) {
        Ok(b) => {
            let b: [u8; 32] = b.try_into().map_err(|_| anyhow::anyhow!("{path}: not 32 bytes"))?;
            Ok(SecretKey::from_bytes(&b))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let k = SecretKey::generate();
            std::fs::write(path, k.to_bytes()).with_context(|| format!("writing {path}"))?;
            Ok(k)
        }
        Err(e) => Err(e).with_context(|| format!("reading {path}")),
    }
}

async fn serve(args: &[String]) -> Result<()> {
    let forward: SocketAddr = opt(args, "--forward").unwrap_or("127.0.0.1:7720").parse()?;
    let allow = opts(args, "--allow")
        .into_iter()
        .map(EndpointId::from_str)
        .collect::<Result<Vec<_>, _>>()?;
    let mut b = Endpoint::builder(presets::N0).alpns(vec![ALPN.to_vec()]);
    if let Some(path) = opt(args, "--key") {
        b = b.secret_key(load_key(path)?);
    }
    let ep = b.bind().await?;
    println!("id {}", ep.id());
    ep.online().await;
    let relay = ep.addr().relay_urls().next().map(|u| u.to_string()).unwrap_or_default();
    println!("online relay={relay}");
    while let Some(incoming) = ep.accept().await {
        let allow = allow.clone();
        tokio::spawn(async move {
            let conn = match async { anyhow::Ok(incoming.accept()?.await?) }.await {
                Ok(c) => c,
                Err(e) => return eprintln!("handshake failed: {e:#}"),
            };
            let peer = conn.remote_id();
            if !allow.is_empty() && !allow.contains(&peer) {
                eprintln!("refused {peer}");
                return conn.close(1u32.into(), b"not allowed");
            }
            eprintln!("peer {peer}");
            while let Ok((send, recv)) = conn.accept_bi().await {
                tokio::spawn(async move {
                    match TcpStream::connect(forward).await {
                        Ok(tcp) => pipe(tcp, send, recv).await,
                        Err(e) => eprintln!("connect {forward}: {e}"),
                    }
                });
            }
        });
    }
    Ok(())
}

/// Copies both ways until each side has ended; a TCP EOF finishes the QUIC send side.
async fn pipe(tcp: TcpStream, mut send: iroh::endpoint::SendStream, mut recv: iroh::endpoint::RecvStream) {
    let (mut tr, mut tw) = tcp.into_split();
    let up = async {
        let n = tokio::io::copy(&mut tr, &mut send).await;
        let _ = send.finish();
        n
    };
    let down = async {
        let n = tokio::io::copy(&mut recv, &mut tw).await;
        let _ = tw.shutdown().await;
        n
    };
    let (a, b) = tokio::join!(up, down);
    eprintln!("stream closed: {:?} up, {:?} down", a.ok(), b.ok());
}

async fn connect(args: &[String]) -> Result<(Endpoint, Connection)> {
    let id = EndpointId::from_str(args.first().context("missing endpoint id")?)?;
    let mut b = Endpoint::builder(presets::N0);
    if args.iter().any(|a| a == "--relay-only") {
        b = b.clear_ip_transports();
    }
    let ep = b.bind().await?;
    let t = Instant::now();
    let conn = ep.connect(id, ALPN).await?;
    eprintln!("connected in {:?}; {}", t.elapsed(), paths(&conn));
    Ok((ep, conn))
}

fn paths(conn: &Connection) -> String {
    conn.paths()
        .iter()
        .map(|p| {
            let kind = if p.is_relay() { "relay" } else { "ip" };
            let sel = if p.is_selected() { "*" } else { "" };
            format!("{sel}{kind} {:?} rtt {:?}", p.remote_addr(), p.rtt())
        })
        .collect::<Vec<_>>()
        .join(", ")
}

async fn dial(args: &[String]) -> Result<()> {
    let listen = opt(args, "--listen").unwrap_or("127.0.0.1:7720");
    let (_ep, conn) = connect(args).await?;
    let l = TcpListener::bind(listen).await?;
    eprintln!("listening on {listen}");
    loop {
        let (tcp, from) = l.accept().await?;
        let (send, recv) = conn.open_bi().await?;
        eprintln!("{from} -> stream; {}", paths(&conn));
        tokio::spawn(pipe(tcp, send, recv));
    }
}

async fn bench(args: &[String]) -> Result<()> {
    let count: usize = opt(args, "--count").unwrap_or("200").parse()?;
    let size: usize = opt(args, "--size").unwrap_or("64").parse()?;
    let (ep, conn) = connect(args).await?;
    // Give hole punching a moment, so the run reports the path it settles on.
    tokio::time::sleep(std::time::Duration::from_secs(3)).await;
    eprintln!("paths: {}", paths(&conn));
    let (mut send, mut recv) = conn.open_bi().await?;
    let msg = vec![0x5a_u8; size];
    let mut back = vec![0_u8; size];
    let mut rtts = Vec::with_capacity(count);
    for _ in 0..count {
        let t = Instant::now();
        send.write_all(&msg).await?;
        recv.read_exact(&mut back).await?;
        rtts.push(t.elapsed().as_secs_f64() * 1e3);
    }
    send.finish()?;
    rtts.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let pct = |p: f64| rtts[((rtts.len() - 1) as f64 * p) as usize];
    println!(
        "{count} x {size} B: p50 {:.2} ms, p90 {:.2} ms, max {:.2} ms; {}",
        pct(0.5),
        pct(0.9),
        pct(1.0),
        paths(&conn)
    );
    conn.close(0u32.into(), b"done");
    ep.close().await;
    Ok(())
}
