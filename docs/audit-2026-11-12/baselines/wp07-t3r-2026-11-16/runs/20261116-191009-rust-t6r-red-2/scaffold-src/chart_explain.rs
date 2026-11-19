//! "Explain this chart": the request the page builds (the shown bars and the drawings) is handed to `athena.charting.explain`, which works out a numbered facts sheet in code
//! and, when a model is available, asks it to put the sheet into sentences that cite it. One at a time, with a time limit; nothing but a fixed message from a failed child reaches
//! the page. The request goes to the child's standard input, so no bar is ever in a command line.
use std::process::Stdio;
use std::time::Duration;

use serde_json::Value;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use tokio::sync::Semaphore;

const LIMIT: Duration = Duration::from_secs(120);
pub const MAX_BYTES: usize = 400 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum ExplainError {
    /// The request is not a JSON object or is too large.
    BadRequest,
    /// Another explanation is running.
    Busy,
    Failed(String),
}

pub struct ChartExplain {
    /// Program and leading arguments.
    module: Vec<String>,
    slot: Semaphore,
}

impl ChartExplain {
    pub fn new(module: Vec<String>) -> Self {
        ChartExplain { module, slot: Semaphore::new(1) }
    }

    pub fn standard(python: &str) -> Self {
        Self::new(vec![python.to_string(), "-m".into(), "athena.charting.explain".into()])
    }

    pub async fn explain(&self, body: &[u8]) -> Result<Value, ExplainError> {
        if body.is_empty() || body.len() > MAX_BYTES || !serde_json::from_slice::<Value>(body).map(|v| v.is_object()).unwrap_or(false) {
            return Err(ExplainError::BadRequest);
        }
        let _slot = self.slot.try_acquire().map_err(|_| ExplainError::Busy)?;
        let program = self.module.first().ok_or_else(|| ExplainError::Failed("the explainer is not configured".into()))?;
        let mut cmd = Command::new(program);
        cmd.args(&self.module[1..]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        let mut child = cmd.spawn().map_err(|_| ExplainError::Failed("could not start the explainer".into()))?;
        let mut stdin = child.stdin.take().ok_or_else(|| ExplainError::Failed("could not start the explainer".into()))?;
        let data = body.to_vec();
        let write = tokio::spawn(async move {
            let _ = stdin.write_all(&data).await;
            drop(stdin); // the child reads to the end of its input
        });
        let output = tokio::time::timeout(LIMIT, child.wait_with_output())
            .await
            .map_err(|_| ExplainError::Failed("the explanation took longer than two minutes and was stopped".into()))?
            .map_err(|_| ExplainError::Failed("could not read the explainer's answer".into()))?;
        let _ = write.await;
        if !output.status.success() {
            return Err(ExplainError::Failed("the explainer failed".into()));
        }
        let doc: Value = serde_json::from_slice(&output.stdout).map_err(|_| ExplainError::Failed("the explainer gave an unreadable answer".into()))?;
        if doc.get("error").is_some() {
            return Err(ExplainError::BadRequest);
        }
        Ok(doc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with(script: &str) -> Option<ChartExplain> {
        std::process::Command::new("sh").arg("-c").arg("true").status().ok()?;
        Some(ChartExplain::new(vec!["sh".into(), "-c".into(), script.into(), "explain".into()]))
    }

    #[tokio::test]
    async fn the_request_reaches_the_child_on_standard_input_and_its_answer_comes_back() {
        // the stand-in echoes how many bytes it was given as a JSON field
        let Some(e) = with("n=$(wc -c); echo \"{\\\"read\\\": null, \\\"bytes\\\": $n}\"") else { return };
        let body = br#"{"symbol":"ABC","bars":[]}"#;
        let got = e.explain(body).await.unwrap();
        assert_eq!(got["bytes"], body.len());
    }

    #[tokio::test]
    async fn a_bad_request_a_failing_child_and_a_busy_slot_are_told_apart() {
        let Some(e) = with("cat >/dev/null; echo '{\"read\": null}'") else { return };
        assert_eq!(e.explain(b"").await, Err(ExplainError::BadRequest));
        assert_eq!(e.explain(b"[1]").await, Err(ExplainError::BadRequest));
        assert_eq!(e.explain(b"not json").await, Err(ExplainError::BadRequest));
        let big = format!("{{\"x\":\"{}\"}}", "a".repeat(MAX_BYTES));
        assert_eq!(e.explain(big.as_bytes()).await, Err(ExplainError::BadRequest));
        let failing = with("cat >/dev/null; exit 3").unwrap();
        assert_eq!(failing.explain(b"{}").await, Err(ExplainError::Failed("the explainer failed".into())));
        let garbled = with("cat >/dev/null; echo nonsense").unwrap();
        assert_eq!(garbled.explain(b"{}").await, Err(ExplainError::Failed("the explainer gave an unreadable answer".into())));
        let refused = with("cat >/dev/null; echo '{\"error\": \"x\"}'").unwrap();
        assert_eq!(refused.explain(b"{}").await, Err(ExplainError::BadRequest));
        let slow = std::sync::Arc::new(with("cat >/dev/null; sleep 1; echo '{}'").unwrap());
        let first = tokio::spawn({
            let slow = slow.clone();
            async move { slow.explain(b"{}").await }
        });
        tokio::time::sleep(Duration::from_millis(200)).await;
        assert_eq!(slow.explain(b"{}").await, Err(ExplainError::Busy));
        assert!(first.await.unwrap().is_ok());
    }
}
