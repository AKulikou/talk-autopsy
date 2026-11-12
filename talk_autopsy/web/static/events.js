// Follows a job's Server-Sent Events. The server replays every event after `after`; on a dropped
// connection EventSource reconnects with Last-Event-ID, and events already seen are ignored.
export function followJob(jobId, after, onEvent, onEnd, onConnection) {
  let last = after;
  let ended = false;
  const source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events?after=${after}`);
  source.onopen = () => onConnection?.(true);
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.id <= last) return;
    last = event.id;
    onEvent(event);
    if (event.type === "finished" || event.type === "failed") {
      ended = true;
      source.close(); // otherwise EventSource reconnects and waits for more
      onEnd(event);
    }
  };
  source.onerror = () => {
    if (ended) return;
    if (source.readyState === EventSource.CLOSED) {
      onEnd({ type: "failed", reason: null, hint: "network" });
    } else {
      onConnection?.(false); // the browser retries on its own
    }
  };
  return () => source.close();
}
