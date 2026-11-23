//! q2-iroh: Car Remote across networks (docs/remote.md#car-remote-across-networks).
//!
//! On the Q2, `serve` keeps an iroh endpoint registered with a relay (n0's public ones unless
//! `--relay` names others) and turns each incoming QUIC stream into a local TCP connection to
//! coda-rc (127.0.0.1:7720), so coda-rc itself is unchanged. demo's Car Remote shim starts it with
//! the daemon and stops it with it (patch/coda_rc_shim.c). `--info` keeps the endpoint's id and
//! home relay in a file, which the daemon (GetRemote) and Pair a Phone's QR code read.
//! `--allow-file` is the allow-list (docs/remote-protocol.md#across-networks-only-paired-phones): the iroh keys of
//! the paired phones, which coda-rc keeps, read at each handshake and each new stream, so a phone forgotten on the Q2
//! is refused from its next stream on; a peer not in it is let in only while one of the `--open-while` files exists
//! (Pair a Phone's code). With `--forward-named` each stream's first 32 bytes to coda-rc are the peer's key, so
//! coda-rc knows which phone came through iroh. `--log` keeps the log to LOG_MAX bytes.
//! On the phone's side (here, a laptop), `dial` listens on a local TCP port and carries each
//! connection to the Q2 as one stream, found by its endpoint id (and `--relay`, `--addr` when
//! known): iroh tries the relay and the direct addresses at once and keeps the first path that
//! works, moving to a direct, hole-punched path when one opens.
//! `bench` sends small messages down one stream and times their echoes.
//!
//!   q2-iroh serve [--key FILE] [--forward ADDR | --forward-named ADDR] [--allow ID]... [--allow-file FILE]
//!                 [--open-while FILE]... [--relay URL]... [--relay-file FILE] [--info FILE] [--pid FILE]
//!                 [--nice N] [--log FILE]
//!   q2-iroh dial ID [--listen ADDR] [--relay URL] [--addr IP:PORT]... [--relay-only] [--key FILE]
//!   q2-iroh bench ID [--count N] [--size BYTES] [--relay URL] [--relay-only] [--key FILE]
//! `--key` on the dialling side keeps its own id, as the phone app keeps its key, so a laptop can stand in for a
//! paired phone that registered it.

use std::{net::SocketAddr, os::unix::fs::DirBuilderExt, str::FromStr, time::Instant};

use anyhow::{bail, Context, Result};
use iroh::{
    endpoint::{presets, Builder, Connection},
    Endpoint, EndpointAddr, EndpointId, RelayMode, RelayUrl, SecretKey, Watcher,
};
use tokio::{
    io::AsyncWriteExt,
    net::{TcpListener, TcpStream},
};

/// The ALPN both sides must name; a peer speaking anything else is refused in the handshake.
const ALPN: &[u8] = b"q2muse/coda-rc/0";

/// The `--log` file is emptied when a line would take it past this, as coda-rc keeps /tmp/coda-rc.log: it is in RAM.
const LOG_MAX: u64 = 64 * 1024;

static LOG: std::sync::OnceLock<String> = std::sync::OnceLock::new();

/// A line to `--log`'s file (opened, appended to and closed, emptied first past LOG_MAX), else to stderr.
fn log(line: &str) {
    match LOG.get() {
        Some(path) => {
            use std::io::Write;
            let full = std::fs::metadata(path).map(|m| m.len() + line.len() as u64 + 1 > LOG_MAX).unwrap_or(false);
            let f = std::fs::OpenOptions::new().create(true).append(!full).write(true).truncate(full).open(path);
            if let Ok(mut f) = f {
                let _ = writeln!(f, "{line}");
            }
        }
        None => eprintln!("{line}"),
    }
}

/// 64 hex digits as the 32 bytes of a key.
fn hex32(s: &str) -> Option<[u8; 32]> {
    let s = s.trim();
    if s.len() != 64 || !s.is_ascii() {
        return None;
    }
    let mut k = [0u8; 32];
    for (i, b) in k.iter_mut().enumerate() {
        *b = u8::from_str_radix(&s[2 * i..2 * i + 2], 16).ok()?;
    }
    Some(k)
}

/// The allow file's text lists key: a line of 64 hex digits each.
fn listed(text: &str, key: &[u8; 32]) -> bool {
    text.lines().filter_map(hex32).any(|k| &k == key)
}

/// Who may come in: `--allow` ids, and with `--allow-file` the file's keys, read at each look, or anyone while an
/// `--open-while` file exists. Neither option: anyone, as before there was a list.
#[derive(Clone)]
struct Gate {
    allow: Vec<EndpointId>,
    file: Option<String>,
    open_while: Vec<String>,
}

impl Gate {
    fn admits(&self, peer: &EndpointId) -> bool {
        if self.allow.is_empty() && self.file.is_none() {
            return true;
        }
        self.allow.contains(peer)
            || self.file.as_ref().is_some_and(|f| listed(&std::fs::read_to_string(f).unwrap_or_default(), peer.as_bytes()))
            || self.open_while.iter().any(|f| std::path::Path::new(f).exists())
    }
}

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
            use std::{io::Write, os::unix::fs::OpenOptionsExt};
            // Made 0600 in a 0700 folder, as coda-rc keeps its own keys (/mnt/data/coda-rc).
            if let Some(dir) = std::path::Path::new(path).parent() {
                std::fs::DirBuilder::new().recursive(true).mode(0o700).create(dir).ok();
            }
            let k = SecretKey::generate();
            let tmp = format!("{path}.new");
            let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp)
                .with_context(|| format!("writing {tmp}"))?;
            f.write_all(&k.to_bytes())?;
            f.sync_all()?;
            std::fs::rename(&tmp, path).with_context(|| format!("renaming {tmp}"))?;
            Ok(k)
        }
        Err(e) => Err(e).with_context(|| format!("reading {path}")),
    }
}

async fn serve(args: &[String]) -> Result<()> {
    if let Some(path) = opt(args, "--log") {
        let _ = LOG.set(path.to_owned());
    }
    let named = opt(args, "--forward-named");
    let forward: SocketAddr = named.or(opt(args, "--forward")).unwrap_or("127.0.0.1:7720").parse()?;
    let named = named.is_some();
    let gate = Gate {
        allow: opts(args, "--allow").into_iter().map(EndpointId::from_str).collect::<Result<Vec<_>, _>>()?,
        file: opt(args, "--allow-file").map(str::to_owned),
        open_while: opts(args, "--open-while").into_iter().map(str::to_owned).collect(),
    };
    if let Some(n) = opt(args, "--nice") {
        extern "C" {
            fn setpriority(which: i32, who: u32, prio: i32) -> i32;
        }
        // SAFETY: setpriority(PRIO_PROCESS, 0, n) only changes this process's own priority.
        unsafe { setpriority(0, 0, n.parse()?) };
    }
    if let Some(path) = opt(args, "--pid") {
        write_atomic(path, format!("{}\n", std::process::id()).as_bytes())?;
    }
    let mut b = relays(Endpoint::builder(presets::N0), args)?.alpns(vec![ALPN.to_vec()]);
    if let Some(path) = opt(args, "--key") {
        b = b.secret_key(load_key(path)?);
    }
    let ep = b.bind().await?;
    println!("id {}", ep.id());
    if let Some(path) = opt(args, "--info") {
        let (path, mut w) = (path.to_owned(), ep.watch_addr());
        tokio::spawn(async move {
            loop {
                if let Err(e) = write_atomic(&path, info(&w.get()).as_bytes()) {
                    log(&format!("{path}: {e:#}"));
                }
                if w.updated().await.is_err() {
                    break;
                }
            }
        });
    }
    ep.online().await;
    let relay = ep.addr().relay_urls().next().map(|u| u.to_string()).unwrap_or_default();
    println!("online relay={relay}");
    while let Some(incoming) = ep.accept().await {
        let gate = gate.clone();
        tokio::spawn(async move {
            let conn = match async { anyhow::Ok(incoming.accept()?.await?) }.await {
                Ok(c) => c,
                Err(e) => return log(&format!("handshake failed: {e:#}")),
            };
            let peer = conn.remote_id();
            if !gate.admits(&peer) {
                log(&format!("refused {peer}"));
                return conn.close(1u32.into(), b"not allowed");
            }
            log(&format!("peer {peer}"));
            while let Ok((send, recv)) = conn.accept_bi().await {
                if !gate.admits(&peer) {
                    // forgotten on the Q2 since the handshake: this stream and the connection end
                    log(&format!("refused {peer}: no longer allowed"));
                    return conn.close(1u32.into(), b"not allowed");
                }
                tokio::spawn(async move {
                    match TcpStream::connect(forward).await {
                        Ok(mut tcp) => {
                            if named && tcp.write_all(peer.as_bytes()).await.is_err() {
                                return;
                            }
                            pipe(tcp, send, recv).await
                        }
                        Err(e) => log(&format!("connect {forward}: {e}")),
                    }
                });
            }
        });
    }
    Ok(())
}

/// The relays: n0's public ones, or those `--relay` names and `--relay-file`'s lines (an own
/// iroh-relay, docs/remote.md#car-remote-across-networks). A missing file names none.
fn relays(b: Builder, args: &[String]) -> Result<Builder> {
    let file = opt(args, "--relay-file").and_then(|p| std::fs::read_to_string(p).ok()).unwrap_or_default();
    let urls = opts(args, "--relay")
        .into_iter()
        .chain(file.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')))
        .map(RelayUrl::from_str)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(if urls.is_empty() { b } else { b.relay_mode(RelayMode::custom(urls)) })
}

/// The `--info` file: `id HEX` and `relay URL` (the home relay, once one is chosen), one per line;
/// coda_rc.c and carremote_page.c read it.
fn info(addr: &EndpointAddr) -> String {
    let mut s = format!("id {}\n", addr.id);
    if let Some(u) = addr.relay_urls().next() {
        s += &format!("relay {u}\n");
    }
    s
}

/// FILE written whole or not at all (a reader never sees half of it), mode 0644.
fn write_atomic(path: &str, data: &[u8]) -> Result<()> {
    let tmp = format!("{path}.new");
    std::fs::write(&tmp, data).with_context(|| format!("writing {tmp}"))?;
    std::fs::rename(&tmp, path).with_context(|| format!("renaming {tmp}"))?;
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
    log(&format!("stream closed: {:?} up, {:?} down", a.ok(), b.ok()));
}

/// Dials the Q2 by its id, through `--relay` and at each `--addr` as well when given: iroh sends on
/// every path it knows at once and uses the first that answers, then the best.
async fn connect(args: &[String]) -> Result<(Endpoint, Connection)> {
    let id = EndpointId::from_str(args.first().context("missing endpoint id")?)?;
    let mut b = relays(Endpoint::builder(presets::N0), args)?;
    if let Some(path) = opt(args, "--key") {
        b = b.secret_key(load_key(path)?);
    }
    if args.iter().any(|a| a == "--relay-only") {
        b = b.clear_ip_transports();
    }
    let mut to = EndpointAddr::new(id);
    for u in opts(args, "--relay") {
        to = to.with_relay_url(RelayUrl::from_str(u)?);
    }
    for a in opts(args, "--addr") {
        to = to.with_ip_addr(a.parse()?);
    }
    let ep = b.bind().await?;
    let t = Instant::now();
    let conn = ep.connect(to, ALPN).await?;
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

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "8aac0bd4c41f2a6e84b5e0d6a3a9b6d8f5c1e2a3b4c5d6e7f8091a2b3c4d5e6f";

    #[test]
    fn the_allow_file_lists_keys_in_hex() {
        let a = hex32(A).unwrap();
        assert!(listed(&format!("{A}\n"), &a));
        assert!(listed(&format!("{}\n{A}\n", "00".repeat(32)), &a));
        assert!(!listed("", &a));
        assert!(!listed(&format!("{}\n", &A[..62]), &a)); // short
        assert!(!listed(&format!("{}zz\n", &A[..62]), &a)); // not hex
        assert!(hex32(&format!(" {A} ")).is_some());
    }

    fn key(b: u8) -> EndpointId {
        SecretKey::from_bytes(&[b; 32]).public()
    }

    fn tmp(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("q2-iroh-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_file(&d);
        d
    }

    #[test]
    fn the_gate_admits_the_listed_and_anyone_while_pairing() {
        let (file, pairing) = (tmp("allow"), tmp("qr"));
        let gate = Gate {
            allow: vec![],
            file: Some(file.to_string_lossy().into()),
            open_while: vec![pairing.to_string_lossy().into()],
        };
        let (a, b) = (key(1), key(2));
        assert!(!gate.admits(&a)); // no file yet: nobody
        let hex: String = a.as_bytes().iter().map(|x| format!("{x:02x}")).collect();
        std::fs::write(&file, format!("{hex}\n")).unwrap();
        assert!(gate.admits(&a) && !gate.admits(&b));
        std::fs::write(&pairing, "123456\n").unwrap();
        assert!(gate.admits(&b)); // Pair a Phone shows its code
        std::fs::remove_file(&pairing).unwrap();
        std::fs::write(&file, "").unwrap(); // forgotten
        assert!(!gate.admits(&a));
        std::fs::remove_file(&file).unwrap();
    }

    #[test]
    fn with_no_list_the_gate_is_open() {
        let gate = Gate { allow: vec![], file: None, open_while: vec![] };
        assert!(gate.admits(&key(3)));
        let gate = Gate { allow: vec![key(4)], file: None, open_while: vec![] };
        assert!(gate.admits(&key(4)) && !gate.admits(&key(3)));
    }

    #[test]
    fn the_log_is_emptied_past_its_limit() {
        let path = tmp("log");
        let _ = LOG.set(path.to_string_lossy().into());
        let line = "x".repeat(1000);
        for _ in 0..200 {
            log(&line);
        }
        let n = std::fs::metadata(&path).unwrap().len();
        assert!(n <= LOG_MAX && n > 0, "{n}");
        std::fs::remove_file(&path).unwrap();
    }
}
