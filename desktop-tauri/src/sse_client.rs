use std::time::Duration;

use futures_util::StreamExt;

pub struct SseClient {
    url: String,
    connected: bool,
    retry_count: u8,
    max_retries: u8,
}

impl SseClient {
    pub fn new(base_url: &str, workspace_id: &str) -> Self {
        Self {
            url: format!("{}/agent-chat?workspace_id={}", base_url, workspace_id),
            connected: false,
            retry_count: 0,
            max_retries: 3,
        }
    }

    pub async fn connect(&mut self) -> Result<(), String> {
        let client = reqwest::Client::new();

        for attempt in 0..=self.max_retries {
            match client
                .get(&self.url)
                .header("Accept", "text/event-stream")
                .send()
                .await
            {
                Ok(resp) if resp.status().is_success() => {
                    self.connected = true;
                    self.retry_count = 0;
                    log::info!("SSE connected to {}", self.url);
                    return Ok(());
                }
                Ok(resp) => {
                    log::warn!("SSE connect failed with status: {}", resp.status());
                }
                Err(e) => {
                    log::warn!("SSE connect error (attempt {}): {}", attempt + 1, e);
                }
            }

            if attempt < self.max_retries {
                let delay = Duration::from_secs(2u64.pow(attempt as u32));
                tokio::time::sleep(delay).await;
                self.retry_count = attempt + 1;
            }
        }

        self.connected = false;
        Err(format!(
            "SSE connection failed after {} retries",
            self.max_retries
        ))
    }

    pub async fn listen<F>(&mut self, mut on_event: F) -> Result<(), String>
    where
        F: FnMut(&str, &str),
    {
        let client = reqwest::Client::new();
        let resp = client
            .get(&self.url)
            .header("Accept", "text/event-stream")
            .send()
            .await
            .map_err(|e| format!("SSE request failed: {}", e))?;

        if !resp.status().is_success() {
            return Err(format!("SSE returned status {}", resp.status()));
        }

        self.connected = true;

        let mut stream = resp.bytes_stream();
        let mut event_type = String::new();
        let mut event_data = String::new();
        let mut line_buffer = String::new();

        while let Some(chunk_result) = stream.next().await {
            let chunk = chunk_result.map_err(|e| format!("SSE stream error: {}", e))?;
            let text = String::from_utf8_lossy(&chunk);
            line_buffer.push_str(&text);

            while let Some(newline_pos) = line_buffer.find('\n') {
                let line = line_buffer[..newline_pos].trim_end_matches('\r').to_string();
                line_buffer = line_buffer[newline_pos + 1..].to_string();

                if line.starts_with("event:") {
                    event_type = line[6..].trim().to_string();
                } else if line.starts_with("data:") {
                    event_data = line[5..].trim().to_string();
                } else if line.is_empty() && !event_type.is_empty() {
                    on_event(&event_type, &event_data);
                    if event_type == "agent_done" {
                        self.connected = false;
                        self.retry_count = 0;
                        return Ok(());
                    }
                    event_type.clear();
                    event_data.clear();
                }
            }
        }

        if !event_type.is_empty() {
            on_event(&event_type, &event_data);
        }

        self.connected = false;
        Ok(())
    }

    pub fn is_connected(&self) -> bool {
        self.connected
    }

    pub fn retry_count(&self) -> u8 {
        self.retry_count
    }
}

pub struct SidecarSseClient {
    url: String,
    connected: bool,
}

impl SidecarSseClient {
    pub fn new(port: u16) -> Self {
        Self {
            url: format!("http://127.0.0.1:{}/api/chat/sse", port),
            connected: false,
        }
    }

    pub async fn connect(&mut self, workspace_id: &str) -> Result<(), String> {
        let url = format!("{}?workspaceId={}", self.url, workspace_id);
        let client = reqwest::Client::new();
        let resp = client
            .get(&url)
            .header("Accept", "text/event-stream")
            .send()
            .await
            .map_err(|e| format!("Sidecar SSE connect failed: {}", e))?;

        if resp.status().is_success() {
            self.connected = true;
            Ok(())
        } else {
            Err(format!("Sidecar SSE returned status {}", resp.status()))
        }
    }

    pub fn is_connected(&self) -> bool {
        self.connected
    }
}
